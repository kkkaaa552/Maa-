# -*- coding: utf-8 -*-
"""作战刷取的「均衡材料决策」Custom action（一个动作，两种 op）。

被两个 pipeline 节点调用：
  · `作战.进入入口`  → custom_action_param = {"op": "reset"}   （清空本任务的已刷记录）
  · `作战.均衡决策`  → custom_action_param = {"op": "decide"}  （读仓库 → 选类别/等级/职业）

决策规则（照使用者给的顺序），分两个阶段：

**阶段一 · 补缺**（只要还有任何一项低于阈值）
  1. 冬谷币      < 2,700,000 → 刷冬谷币
  2. 深度社会学  < 587       → 刷教材
  3. 核定牌：**每个职业的每一级都要达到阈值**（20/30/30/35/85 是「级别 × 职业」的矩阵，
     不是级别合计）。判定顺序：等级 I→V；同等级内按「该级当前数量最少」优先，
     数量并列时按 远击 → 轻锐 → 宿卫 → 构术 → 战略 的顺序。
  某一目标本轮已经刷过就跳过（DONE 记录），于是多轮会依次覆盖不同材料 —— 这就是「均衡」。

**阶段二 · 轮换**（mode=balance 时，进入条件见下）
  不再收尾，改按固定的 **冬谷币 → 教材 → 核定牌 → 冬谷币 → …** 顺序循环继续刷，
  每轮一个类别；轮到核定牌时，按「等级 I→V、同级数量最少优先、并列按职业序」的全量表
  依次取格（5 等级 × 5 职业 = 25 格轮完一轮再回到第一格），保证不会永远只刷同一格。
  停止条件只剩两个：`作战.本轮计数` 的 max_hit（= option「刷取轮数」）用尽，或活力值不足。

  进入轮换有两个触发点，都有意保留：
  a. **三项阈值全部达标**（这正是操作者要求的「材料均大于给出的数量」那条）；
  b. **阶段一把不足项各刷了一遍、但读数仍未达标**。原因见「数据前提」第 1 条：快照是静态
     文件，刷完数字不会变，所以 (a) 在本轮内其实很难成立（实测冬谷币 237 万 < 270 万即如此）。
     只认 (a) 会让轮换在本轮里永不触发，等于没有效果。所以 (b) 也进轮换，把「还要不要继续」
     交给「刷取轮数」和活力值来兜，而不是被一份过期快照卡死。
  mode=balance-strict 只认 (a)，保住「没全达标就不动」的旧语义。
  mode=manual（操作者选了「手动指定」）时保持旧行为：全达标或不足项刷完即收尾。

## 三条必须写进日志的数据前提（都是实测出来的）

1. **仓库快照是静态文件，刷完后数字不会自己变**。`decide` 每轮读到的都是同一份
   `data\\warehouse\\*.json`，所以「刷了多少」不会反馈到读数里。DONE 机制是唯一的
   收敛保证：同一个（类别, 等级, 职业）本轮只刷一次，刷完全部不足项就收尾。
   要让判定跟上真实库存，得在轮次之间重跑一次「仓库识别」任务。
2. **数量是带中文单位的字符串**。实测票券页的 `冬谷币2` count 是 `"56.27万"`，
   `int()` 会炸；统一走 parse_count 处理「万/亿/千分位逗号」。
3. **同名材料可能占多格**（实测 `轻锐核定牌I` 出现 8 与 61 两次，`冬谷币` 带 OCR 尾缀 `2`）。
   按归一化后的名字**累加**，不取首条。

## 关于「读到多少格」

`cells` 是翻页累积的结果，`icons` 是模板库文件数（169），两者数量级本来就不该相等，
不能拿 `cells < icons` 判定漏读。真正的漏读标志是：末行仍然满 6 格却没有触发
`BOTTOM_PX` 到底判定（`agent/warehouse_agent.py:213`）。实测三份快照的末行都只有
1~3 格命中（票券 r2 仅 1 格），属于已经翻到底，不是被截断。

阈值与等级/职业模板名全部写在文件顶部常量里，改规则不用动 pipeline。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "warehouse"

# ── 阈值（可在 custom_action_param 里覆盖） ────────────────────────────
# 核定牌：每个职业的每一级都要达到这个数字
THRESHOLDS = {
    "冬谷币": 2_700_000,
    "深度社会学": 587,
    "核定牌": {"I": 20, "II": 30, "III": 30, "IV": 35, "V": 85},
}

# 等级：罗马数字 ↔ 地支。最高是 辰(V)，默认刷最高。
LEVELS = [("I", "子"), ("II", "丑"), ("III", "寅"), ("IV", "卯"), ("V", "辰")]
LEVEL_BY_ROMAN = dict(LEVELS)
ROMAN_BY_ZHI = {z: r for r, z in LEVELS}
HIGHEST_ZHI = "辰"

# 职业顺序（核定牌）
PROFESSIONS = ["远击", "轻锐", "宿卫", "构术", "战略"]

# 模板名（与 pipeline 里 作战.选类别 / 作战.选等级 / 作战.选职业 的占位一致）
TPL_CATEGORY = {
    "冬谷币": "作战.培养_冬谷币.png",
    "教材": "作战.培养_教材.png",
    "核定牌": "作战.考核.png",
}
TPL_LEVEL = {r: f"作战.等级_{z}.png" for r, z in LEVELS}
TPL_PROF = {p: f"作战.职业_{p}.png" for p in PROFESSIONS}

# ── 固定坐标（1280x720，2026-11-23 实机截图量的，不是估的）──────────────
# 为什么用坐标而不是模板：等级模板全部裁自考核页（白色椭圆数字），冬谷币/教材页是
# 彩色横幅上的白字，一套模板覆盖不了三套页面；类别页签的选中/未选中两态也没法用同一张图。
# 左栏类别行（培养组展开态，x 恒 110）：培养 105 / 冬谷币 185 / 装备 260 / 教材 337 / 场域 410 / 考核 485
CATEGORY_XY = {
    "冬谷币": [110, 185],
    "教材": [110, 337],
    "核定牌": [110, 485],
}
# 三套页面的五张关卡卡中心
LEVEL_XY = {
    "冬谷币": {"I": [420, 260], "II": [477, 485], "III": [745, 257], "IV": [800, 482], "V": [1085, 320]},
    "教材":   {"I": [407, 215], "II": [560, 505], "III": [750, 210], "IV": [880, 510], "V": [1160, 337]},
    "核定牌": {"I": [310, 297], "II": [522, 387], "III": [765, 297], "IV": [995, 387], "V": [1210, 297]},
}
# 考核页左栏职业行（x 恒 110）
PROF_XY = {
    "宿卫": [110, 247],
    "构术": [110, 315],
    "远击": [110, 382],
    "轻锐": [110, 450],
    "战略": [110, 522],
}

# 本任务内已刷过的目标（由 op=reset 清空）
DONE: set[str] = set()

_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([万萬亿億]?)")
_PLATE_RE = re.compile(r"^(远击|轻锐|宿卫|构术|战略)核定牌(IV|V|III|II|I)$")


def log(msg: str) -> None:
    print(f"[combat_balance] {msg}", flush=True)


# ── 数量与名字归一化 ──────────────────────────────────────────────────
def parse_count(raw) -> int | None:
    """'56.27万' / '1,234' / '22' / 22 → int；解析不出返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip().replace(",", "").replace("，", "")
    if not s:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    if unit in ("万", "萬"):
        val *= 10_000
    elif unit in ("亿", "億"):
        val *= 100_000_000
    return int(round(val))


def norm_name(name: str) -> str:
    """去 OCR 尾缀噪声：'冬谷币2' → '冬谷币'；罗马数字统一成 ASCII。"""
    s = (name or "").strip()
    for a, b in (("Ⅰ", "I"), ("Ⅱ", "II"), ("Ⅲ", "III"), ("Ⅳ", "IV"), ("Ⅴ", "V")):
        s = s.replace(a, b)
    if len(s) > 2 and s[-1].isdigit() and not s[-2].isdigit():
        stripped = s[:-1]
        # 只在去掉后不是已知的「自选包II」这类名字时去尾缀
        if not stripped.endswith("自选包"):
            s = stripped
    return s


# ── 读快照 ────────────────────────────────────────────────────────────
def read_inventory() -> tuple[dict[str, int], dict]:
    counts: dict[str, int] = {}
    diag: dict = {"files": [], "cells": 0, "icons": None,
                  "shots": set(), "ts": [], "unparsed": [], "missing": [],
                  "status": {"hit": 0, "uncertain": 0, "empty": 0}}
    for f in ("物料.json", "资源.json", "票券.json"):
        p = DATA / f
        if not p.exists():
            diag["missing"].append(f)
            diag["files"].append(f"{f}: 缺失")
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            diag["files"].append(f"{f}: 读取失败 {e}")
            continue
        cells = d.get("cells") or []
        diag["files"].append(f"{f}: {len(cells)} 格")
        diag["cells"] += len(cells)
        if d.get("icons"):
            diag["icons"] = d["icons"]
        if d.get("shot"):
            diag["shots"].add(d["shot"])
        if d.get("ts"):
            diag["ts"].append(d["ts"])
        for c in cells:
            st = c.get("status")
            if st in diag["status"]:
                diag["status"][st] += 1
            if st != "hit":
                continue
            n = norm_name(c.get("name", ""))
            if not n:
                continue
            raw = c.get("count_final")
            if raw is None:
                raw = c.get("count")
            v = parse_count(raw)
            if v is None:
                diag["unparsed"].append(f"{f}:{c.get('name')}={raw!r}")
                continue
            counts[n] = counts.get(n, 0) + v
    return counts, diag


# ── 核定牌：级别 × 职业 ───────────────────────────────────────────────
def plate_totals(counts: dict[str, int]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """核定牌：按等级合计 + 按(职业→等级)明细。"""
    by_level: dict[str, int] = {r: 0 for r, _ in LEVELS}
    by_prof: dict[str, dict[str, int]] = {}
    for name, v in counts.items():
        m = _PLATE_RE.match(name)
        if not m:
            continue
        prof, lvl = m.group(1), m.group(2)
        by_level[lvl] = by_level.get(lvl, 0) + v
        by_prof.setdefault(prof, {})
        by_prof[prof][lvl] = by_prof[prof].get(lvl, 0) + v
    return by_level, by_prof


def plate_candidates(by_prof: dict[str, dict[str, int]], limits: dict
                     ) -> list[tuple[str, str, int, int]]:
    """逐个 (等级, 职业) 判定缺不缺，返回 [(roman, prof, have, need)]。

    顺序：等级 I→V 外层；同等级内按该级当前数量升序，数量并列时按 PROFESSIONS 顺序。
    这就是「挑数量最少的职业先刷」，且最终每个职业都会补到阈值。
    """
    out: list[tuple[str, str, int, int]] = []
    for roman, _zhi in LEVELS:
        need = int(limits.get(roman, 0) or 0)
        if need <= 0:
            continue
        row = [(by_prof.get(p, {}).get(roman, 0), i, p) for i, p in enumerate(PROFESSIONS)]
        row.sort()
        for have, _i, p in row:
            if have < need:
                out.append((roman, p, have, need))
    return out


# ── 轮换（全部达标之后继续刷） ─────────────────────────────────────────
# 操作者指定的固定顺序：冬谷币 → 教材 → 核定牌 → 回到冬谷币 …
ROTATION = ["冬谷币", "教材", "核定牌"]

# 轮换指针：_rot_pos 指向下一个类别，_plate_pos 指向核定牌全量队列里的下一格。
# 两者都由 op=reset 清零，同一个任务内连续推进。
_rot_pos = 0
_plate_pos = 0


def rotation_reset() -> None:
    global _rot_pos, _plate_pos
    _rot_pos = 0
    _plate_pos = 0


def all_met(counts: dict[str, int], th: dict) -> bool:
    """三项阈值是不是全都已经达到（不是「本轮是否覆盖过」）。"""
    if counts.get("冬谷币", 0) < int(th.get("冬谷币", THRESHOLDS["冬谷币"])):
        return False
    if counts.get("深度社会学", 0) < int(th.get("深度社会学", THRESHOLDS["深度社会学"])):
        return False
    limits: dict = th.get("核定牌") or THRESHOLDS["核定牌"]
    _by_level, by_prof = plate_totals(counts)
    return not plate_candidates(by_prof, limits)


def plate_cycle(by_prof: dict[str, dict[str, int]]) -> list[tuple[str, str]]:
    """核定牌的全量队列：等级 I→V 外层，同级按数量升序、并列按职业序。共 25 格。"""
    cyc: list[tuple[str, str]] = []
    for roman, _zhi in LEVELS:
        row = sorted([(by_prof.get(p, {}).get(roman, 0), i, p)
                      for i, p in enumerate(PROFESSIONS)])
        cyc.extend((roman, p) for _have, _i, p in row)
    return cyc


def rotation_pick(counts: dict[str, int], th: dict):
    """全达标后的轮换：返回 (类别, 等级, 职业或None, 理由)，每调一次前进一格。

    顺序固定为 ROTATION，核定牌那一格内部再按 plate_cycle 走满 25 格才回到下一次。
    队列为空（仓库里一个核定牌格都没读到）时返回 None，由调用方决定收尾。
    """
    global _rot_pos, _plate_pos
    category = ROTATION[_rot_pos % len(ROTATION)]
    nth = _rot_pos + 1

    if category == "核定牌":
        _by_level, by_prof = plate_totals(counts)
        cyc = plate_cycle(by_prof)
        if not cyc:
            return None
        idx = _plate_pos % len(cyc)
        roman, prof = cyc[idx]
        have = by_prof.get(prof, {}).get(roman, 0)
        why = (f"轮换第 {nth} 位 · 核定牌 {roman}({LEVEL_BY_ROMAN[roman]})·{prof}"
               f"（当前 {have}，全量队列第 {idx + 1}/{len(cyc)} 格）")
        _plate_pos += 1
        _rot_pos += 1
        return ("核定牌", roman, prof, why)

    if category == "冬谷币":
        why = f"轮换第 {nth} 位 · 冬谷币（当前 {counts.get('冬谷币', 0):,}，已达标）"
    else:
        why = (f"轮换第 {nth} 位 · 教材（深度社会学 当前 "
               f"{counts.get('深度社会学', 0)}，已达标）")
    _rot_pos += 1
    return (category, "V", None, why)


# ── 决策 ──────────────────────────────────────────────────────────────
def decide(counts: dict[str, int], th: dict, done: set[str] | None = None):
    """返回 (类别, 等级罗马字, 职业或None, 理由)；全达标或已刷完返回 None。"""
    done = done or set()

    if "冬谷币" not in done:
        have = counts.get("冬谷币", 0)
        need = int(th.get("冬谷币", THRESHOLDS["冬谷币"]))
        if have < need:
            return ("冬谷币", "V", None, f"冬谷币 {have:,} < 阈值 {need:,}")

    if "教材" not in done:
        have = counts.get("深度社会学", 0)
        need = int(th.get("深度社会学", THRESHOLDS["深度社会学"]))
        if have < need:
            return ("教材", "V", None, f"深度社会学 {have} < 阈值 {need}")

    limits: dict = th.get("核定牌") or THRESHOLDS["核定牌"]
    _by_level, by_prof = plate_totals(counts)
    cands = plate_candidates(by_prof, limits)
    if not cands:
        return None
    for roman, prof, have, need in cands:
        key = f"核定牌-{roman}-{prof}"
        if key in done:
            continue
        zhi = LEVEL_BY_ROMAN[roman]
        return ("核定牌", roman, prof,
                f"核定牌 {roman}({zhi})·{prof} = {have} < 阈值 {need}"
                f"（该级各职业：{_prof_row(by_prof, roman)}）")
    return None


def _prof_row(by_prof: dict[str, dict[str, int]], roman: str) -> str:
    return "/".join(f"{p}={by_prof.get(p, {}).get(roman, 0)}" for p in PROFESSIONS)


# ── 注册 ──────────────────────────────────────────────────────────────
def register() -> None:
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction

    @AgentServer.custom_action("作战均衡决策")
    class CombatBalance(CustomAction):
        def run(self, context, argv) -> bool:
            ok = CustomAction.RunResult(success=True)

            try:
                params = argv.custom_action_param
                if isinstance(params, str):
                    params = json.loads(params) if params.strip() else {}
                params = params or {}
            except Exception:  # noqa: BLE001
                params = {}
            op = str(params.get("op") or "decide")

            # ── reset：清本任务的已刷记录 ──
            if op == "reset":
                DONE.clear()
                rotation_reset()
                log("reset：已刷记录清空，本任务重新开始均衡")
                return ok

            # ── decide ──
            mode = str(params.get("mode") or "balance")
            th = {
                "冬谷币": params.get("donggu_limit", THRESHOLDS["冬谷币"]),
                "深度社会学": params.get("shendu_limit", THRESHOLDS["深度社会学"]),
                "核定牌": params.get("plate_limits", THRESHOLDS["核定牌"]),
            }
            counts, diag = read_inventory()
            log("读数 " + "; ".join(diag["files"]))
            if diag["ts"]:
                log(f"快照 ts={diag['ts']} 页码={sorted(diag['shots'])}")
            st = diag["status"]
            log(f"格状态 hit={st['hit']} uncertain={st['uncertain']} empty={st['empty']}")
            if st["uncertain"]:
                log(f"!! {st['uncertain']} 格名字存疑，未计入统计（可能让判定偏严）")
            if diag["missing"]:
                log(f"!! 缺文件 {diag['missing']}")
            if diag["unparsed"]:
                log(f"!! 数量解析失败 {len(diag['unparsed'])} 条：{diag['unparsed'][:5]}")
            if not counts:
                log("!! 仓库读数为空，无法均衡决策 → 退回手动所选类别")
                context.override_next("作战.均衡决策", ["作战.选类别"])
                return ok

            if all_met(counts, th):
                # 三项阈值全部达到。均衡模式下不收尾，按固定顺序继续轮换刷。
                if mode not in ("balance", "balance-strict"):
                    log("全部达标（手动指定模式），结束刷取")
                    context.override_next("作战.均衡决策", ["作战.达标收尾"])
                    return ok
                log("三项阈值均已达标 → 进入轮换：冬谷币 → 教材 → 核定牌（循环）")
                picked = rotation_pick(counts, th)
                if picked is None:
                    log("核定牌队列为空（仓库没读到任何核定牌格），无法轮换 → 结束刷取")
                    context.override_next("作战.均衡决策", ["作战.达标收尾"])
                    return ok
                rotation = True
            else:
                picked = decide(counts, th, DONE)
                rotation = False
                if picked is None:
                    # 本任务的不足项都已各刷过一遍，但读数里仍有项没到阈值。
                    # 仓库快照是静态文件（见模块头「三条数据前提」第 1 条），再判也还是这批
                    # 结果，继续走 decide 只会立刻又返回 None。均衡模式下转轮换继续刷，
                    # 由「刷取轮数」的 max_hit 与活力值兜住上限，不会无限跑。
                    # mode=balance-strict 时保留旧行为：没全达标就不轮换，直接收尾。
                    if mode == "balance":
                        log("不足项已各刷一遍，读数未更新 → 转入轮换继续刷")
                        picked = rotation_pick(counts, th)
                        if picked is None:
                            log("核定牌队列为空，无法轮换 → 结束刷取")
                            context.override_next("作战.均衡决策", ["作战.达标收尾"])
                            return ok
                        rotation = True
                    else:
                        log("本轮目标已覆盖且未全达标（严格模式），结束刷取")
                        context.override_next("作战.均衡决策", ["作战.达标收尾"])
                        return ok

            category, roman, prof, why = picked
            log(("轮换 → " if rotation else "决策 → ")
                + f"「{category}」{roman}({LEVEL_BY_ROMAN[roman]})"
                + (f" · {prof}" if prof else "") + f"：{why}")

            cat_xy = CATEGORY_XY[category]
            lv_xy = LEVEL_XY[category][roman]
            log(f"坐标 类别={cat_xy} 等级={lv_xy}"
                + (f" 职业={PROF_XY[prof]}" if category == "核定牌" else ""))
            patch: dict = {
                "作战.选类别": {"recognition": {"type": "DirectHit", "param": {}},
                                "action": {"type": "Click", "param": {"target": cat_xy}}},
                "作战.选等级": {"recognition": {"type": "DirectHit", "param": {}},
                                "action": {"type": "Click", "param": {"target": lv_xy}}},
            }
            if category == "核定牌":
                patch["作战.选职业"] = {
                    "recognition": {"type": "DirectHit", "param": {}},
                    "action": {"type": "Click", "param": {"target": PROF_XY[prof]}},
                }
                if not rotation:
                    DONE.add(f"核定牌-{roman}-{prof}")
            else:
                # 非核定牌没有职业页签：保持直通，别让它空等
                patch["作战.选职业"] = {
                    "recognition": {"type": "DirectHit", "param": {}},
                    "action": {"type": "DoNothing", "param": {}},
                }
                if not rotation:
                    DONE.add(category)

            context.override_pipeline(patch)
            context.override_next("作战.均衡决策", ["作战.选类别"])
            return ok


def selftest() -> int:
    """不连设备：只验证读数、阈值、决策三条。"""
    log("=" * 60)
    counts, diag = read_inventory()
    log("诊断 " + json.dumps({k: (sorted(v) if isinstance(v, set) else v)
                              for k, v in diag.items()}, ensure_ascii=False)[:600])
    log(f"命中材料 {len(counts)} 种")
    for k in ("冬谷币", "深度社会学", "进阶社会学"):
        log(f"  {k} = {counts.get(k)}")

    by_level, by_prof = plate_totals(counts)
    log(f"  核定牌按等级合计 {by_level}")
    for roman, zhi in LEVELS:
        log(f"    {roman}({zhi}) 需 {THRESHOLDS['核定牌'][roman]}：{_prof_row(by_prof, roman)}")

    cands = plate_candidates(by_prof, THRESHOLDS["核定牌"])
    log(f"  不足的（等级,职业）共 {len(cands)} 项："
        + "、".join(f"{r}·{p}({h}/{n})" for r, p, h, n in cands))

    DONE.clear()
    rotation_reset()
    for i in range(1, 40):
        picked = decide(counts, THRESHOLDS, DONE)
        if picked is None:
            log(f"第 {i} 轮：本轮目标已覆盖 → 阶段一结束")
            break
        c, r, prof, why = picked
        log(f"第 {i} 轮 → {c} {r}({LEVEL_BY_ROMAN[r]})"
            + (f" · {prof}" if prof else "") + f" | {why}")
        DONE.add(f"核定牌-{r}-{prof}" if c == "核定牌" else c)
    log(f"本轮共安排 {len(DONE)} 个目标：{sorted(DONE)}")

    # ── 阶段二：轮换 ──
    met = all_met(counts, THRESHOLDS)
    log(f"是否全部达标 all_met={met}")
    rotation_reset()
    by_level, by_prof = plate_totals(counts)
    cyc = plate_cycle(by_prof)
    log(f"核定牌轮换队列 {len(cyc)} 格："
        + "、".join(f"{r}·{p}" for r, p in cyc))
    for i in range(1, 12):
        p = rotation_pick(counts, THRESHOLDS)
        if p is None:
            log(f"轮换第 {i} 步 → 队列为空，收尾")
            break
        c, r, prof, why = p
        log(f"轮换第 {i} 步 → {c} {r}({LEVEL_BY_ROMAN[r]})"
            + (f" · {prof}" if prof else "") + f" | {why}")
        DONE.add(f"核定牌-{r}-{prof}" if c == "核定牌" else c)
    log(f"轮换后已安排 {len(DONE)} 个目标：{sorted(DONE)}")
    log("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
