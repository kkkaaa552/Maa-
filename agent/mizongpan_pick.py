# -*- coding: utf-8 -*-
"""迷踪盘选关 Custom action：在关卡列表里翻找目标关卡并点进去。

为什么必须收进 Python（2026-11-26 实测教训）：
    起初用 pipeline 的 `选关 -on_error-> 翻页 -next-> 选关` 做「找不到就翻页」，
    Maafw 的环路检测直接把这条 on_error 回路判成 error handling loop：
        [ERR][PipelineTask.cpp][L80] error handling loop detected [node.name=迷踪盘.归顶]
    任务在第一次滑动之后就被终止（22.6s，succeeded=False）。同理 `归顶` 也会被判定成环。
    所以「识别失败 → 滑动 → 重新识别」这个循环只能在 Custom action 里写，
    pipeline 侧只保留一个节点挂本动作。

两种模式：
    单关模式  custom_action_param = {"level": N}          —— 只选第 N 关，跑完不管后续
    连刷模式  custom_action_param = {"levels": [1..30]}   —— 每次调用推进一关：
               · 取下一关号 → `context.override_pipeline` 改 `迷踪盘.点位` 的 level
               · 选中并点进去 → 返回 True，pipeline 继续走 前往/点位/完成/下一关
               · 列表耗尽 → 返回 False，pipeline 走 on_error 收尾退大厅
               · 本关没找到 → 记进失败清单、**仍返回 True 并推进**，让 前往 等不到按钮
                 自己 on_error 回 选关，等于跳过这一关（单关失败不中断整串）

    为什么要这样切（15:31 / 15:35 两次实测）：
        一开始把整个连刷写成一个大 Custom action（选关→前往→点位→完成 全在 Python 里
        循环），两次都死在「选关点完卡片之后再截图」这一步，主进程报
        `query_controller  controller not found [controller_id=]`，
        agent 侧 controller 代理句柄失效、任务卡死。而 `mizongpan_pts.run_points`
        那种「一次进去一口气跑完就返回」的用法 26 关全过 —— 差别是它不在一次 action
        里做多轮「截图→动作→再截图→再动作」的跨步骤循环。
        所以关卡循环交回 pipeline 的 next 回路（Maafw 支持），一个 Custom action
        只做一件有界的事。

列表顺序（temp/_probe_order.py 实测，OCR roi=[150,190,520,420]，共 61 项）：
    体验关卡 → 第31关 … 第60关 → 第1关 … 第30关
即已通关的关卡沉在底部，所以找第 1–30 关必须先归顶、再向下翻。

滑动步长（temp/_probe_step.py 实测）：
    begin (430,520) → end (430,320)，duration 900ms，post 800ms
    每次约跨 4 个关卡项、与上一屏有重叠（顶屏 体验/31/32/33+34/35/36/37 → 1 滑 38..41+42..45），
    不会漏项。dy=200/ms=900 是四档里唯一「跨 8 项且与上屏重叠」的档。

归顶动作：
    begin (430,280) → end (430,520)，duration 900ms，post 700ms（内容下移）
    从第 22 关附近拖 4 次即到顶；到顶后再拖无变化，用 OCR 文本集去重判定到底/到顶。
"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "debug" / "mt_pts.log"
sys.path.insert(0, str(ROOT / "tools"))

# 关卡列表的可视区（不含顶部标题条与底部按钮）
LIST_ROI = (150, 190, 520, 420)
OCR_THR = 0.30

SWIPE_DOWN = ((430, 280), (430, 520))   # 内容下移 = 往顶部翻
SWIPE_UP = ((430, 520), (430, 320))     # 内容上移 = 往下翻
DURATION = 900
SETTLE = 0.80

TOP_MAX = 8        # 归顶最多拖 8 次（实测 4–6 次够）
SCAN_MAX = 20      # 归顶后向下最多翻 20 屏（61 项 / 8 项每屏 ≈ 8 屏，留足余量）
NOCHG_LIMIT = 2    # 文本集连续不变 2 次判为到底

# 连刷进度：只在同一轮任务里有效。key 换成新的关卡串就重置。
_SEQ: dict = {"key": None, "levels": [], "i": 0, "ok": [], "fail": [], "t0": 0.0}


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def want_text(level: int) -> str:
    return "体验关卡" if level <= 0 else f"第{level}关"


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def hit_target(txt: str, level: int) -> bool:
    """严格等值匹配，绝不做子串判断。

    历史缺陷：interface 里 expected 写成 ["第2关","2关"]，MaaFramework 的 OCR
    会拿短串「2关」去匹配「第32关」并判 0.999979 命中 —— 第 2 关实际点到了第 32 关。
    这里用整串正则锚定，`第2关` 不会命中 `第32关`，`第1关` 不会命中 `第11关`/`第21关`。
    """
    t = norm(txt)
    if level <= 0:
        return t == "体验关卡"
    m = re.fullmatch(r"第0*(\d+)关", t)
    return bool(m) and int(m.group(1)) == level


def read_list(context, img) -> list:
    """整块列表 OCR，返回 [(text, score, (x, y, w, h))]。

    必须用 `context.run_recognition_direct`（同步返回），**不能用
    `tasker.post_recognition(...).wait()`**：后者在 Custom action 的执行线程里
    会去等同一个 tasker 的任务队列，而队列正被本 action 占着 —— 2026-11-26 14:52
    实测直接死锁，任务卡 6 分钟无任何节点输出。
    """
    from maa.pipeline import JOCR, JRecognitionType

    reco = context.run_recognition_direct(
        JRecognitionType.OCR,
        JOCR(roi=list(LIST_ROI), expected=[], threshold=0.1),
        img,
    )
    rows = []
    for it in ([] if reco is None else list(reco.all_results or [])):
        t = getattr(it, "text", "")
        if not t:
            continue
        sc = float(getattr(it, "score", 0.0))
        if sc < OCR_THR:
            continue
        b = getattr(it, "box", None)
        box = (int(b[0]), int(b[1]), int(b[2]), int(b[3])) if b else (0, 0, 0, 0)
        rows.append((t, sc, box))
    return rows


def run_pick(context, level: int) -> tuple:
    """把列表翻到目标关卡并点进去。返回 (ok, msg)。

    四段式翻找：当前屏 → 向下翻到底 → 归顶 → 再向下找。

    为什么不是「先归顶再往下翻」（v1 写法）：连刷 1–30 关时关卡号单调递增，
    选完第 4 关后列表正停在附近，第 5 关几乎总在下一屏；先归顶等于每关白滑 4–6 次，
    单关浪费 10–25s（实测第 2 关 5.3s、第 3 关 14.4s、第 30 关 25.4s，全耗在归顶上）。

    为什么仍要归顶兜底：关卡列表是「体验关卡 → 第31..60关 → 第1..30关」，
    机器人可能在任意滚动位置启动（上一轮跑完停在第 30 关附近），只往下探会漏掉上方的项。

    截图/滑动/点击走 controller（`mizongpan_pts.py` 在同一 Custom action 里用同一套
    接口跑过 26 关，安全）；OCR 走 `context.run_recognition_direct`（同步返回）。
    **不能用 `tasker.post_recognition(...).wait()`** —— 那会去等同一个 tasker 的任务
    队列，而队列正被本 action 占着，2026-11-26 14:52 实测直接死锁，任务卡 6 分钟
    无任何节点输出。
    """
    ctl = context.tasker.controller

    def grab():
        img = ctl.post_screencap().wait().get()
        if img is None:
            raise RuntimeError("截图返回空")
        return img

    def swipe(begin, end):
        ctl.post_swipe(int(begin[0]), int(begin[1]),
                       int(end[0]), int(end[1]), DURATION).wait()
        time.sleep(SETTLE)

    def click(x, y):
        ctl.post_click(int(x), int(y)).wait()

    def scan_once():
        img = grab()
        rows = read_list(context, img)
        for t, sc, box in rows:
            if hit_target(t, level):
                return (t, sc, box), rows
        return None, rows

    t0 = time.time()
    target = want_text(level)
    hit = None
    tops = 0
    down1 = 0
    down2 = 0

    # 1) 当前屏
    try:
        hit, rows = scan_once()
    except Exception:
        log("迷踪盘选关异常：\n" + traceback.format_exc())
        return False, "选关首次读屏失败"
    if hit:
        log(f"  当前屏即命中 {target!r} score={hit[1]:.3f} box={hit[2]}")
    else:
        # 2) 向下翻到底（列表已通关沉底，目标常在下方）
        prev = {norm(t) for t, _s, _b in rows}
        nochg = 0
        for i in range(SCAN_MAX):
            swipe(*SWIPE_UP)
            down1 = i + 1
            try:
                _hit, rows = scan_once()
            except Exception:
                log("迷踪盘选关翻页读屏异常：\n" + traceback.format_exc())
                return False, "翻页读屏失败"
            if _hit:
                hit = _hit
                log(f"  向下第 {down1} 屏命中 {target!r} score={_hit[1]:.3f} box={_hit[2]}")
                break
            cur = {norm(t) for t, _s, _b in rows}
            nochg = nochg + 1 if cur == prev else 0
            prev = cur
            if nochg >= NOCHG_LIMIT:
                log(f"  向下翻到底（连续 {nochg} 屏无变化）")
                break

        if hit is None:
            # 3) 归顶：连续拖动直到文本集不再变化
            prev = {norm(t) for t, _s, _b in rows}
            for i in range(TOP_MAX):
                swipe(*SWIPE_DOWN)
                tops = i + 1
                try:
                    _hit, rows = scan_once()
                except Exception:
                    log("迷踪盘选关归顶读屏异常：\n" + traceback.format_exc())
                    return False, "归顶读屏失败"
                if _hit:
                    hit = _hit
                    log(f"  归顶第 {tops} 次后命中 {target!r} score={_hit[1]:.3f} box={_hit[2]}")
                    break
                cur = {norm(t) for t, _s, _b in rows}
                if cur == prev:
                    log(f"  归顶完成（第 {tops} 次后文本集不再变化，{len(cur)} 项）")
                    break
                prev = cur

        if hit is None:
            # 4) 归顶后重新逐屏向下找
            prev = {norm(t) for t, _s, _b in rows}
            nochg = 0
            for i in range(SCAN_MAX):
                swipe(*SWIPE_UP)
                down2 = i + 1
                try:
                    _hit, rows = scan_once()
                except Exception:
                    log("迷踪盘选关二次翻页读屏异常：\n" + traceback.format_exc())
                    return False, "二次翻页读屏失败"
                if _hit:
                    hit = _hit
                    log(f"  归顶后第 {down2} 屏命中 {target!r} score={_hit[1]:.3f} box={_hit[2]}")
                    break
                cur = {norm(t) for t, _s, _b in rows}
                nochg = nochg + 1 if cur == prev else 0
                prev = cur
                if nochg >= NOCHG_LIMIT:
                    log(f"  归顶后翻到底（连续 {nochg} 屏无变化）")
                    break

    if hit is None:
        return False, (f"列表翻到底也没找到 {target}"
                       f"（下翻 {down1} 屏 / 归顶 {tops} 次 / 再下翻 {down2} 屏，"
                       f"用时 {time.time() - t0:.1f}s）")

    t, sc, box = hit
    cx, cy = box[0] + box[2] // 2, box[1] + box[3] // 2
    click(cx, cy)
    time.sleep(1.2)
    log(f"  已点击 {t!r} 中心 ({cx},{cy})，用时 {time.time() - t0:.1f}s")
    return True, f"选中 {t}（score {sc:.3f}，{time.time() - t0:.1f}s）"


def _seq_next(context, levels: list) -> tuple:
    """连刷模式：推进到下一关，返回 (level, continue_, msg)。"""
    key = tuple(levels)
    if _SEQ["key"] != key:
        _SEQ.update(key=key, levels=list(levels), i=0, ok=[], fail=[], t0=time.time())
        log(f"迷踪盘连刷 启动：{len(levels)} 关 {levels[0]}→{levels[-1]}")

    i = _SEQ["i"]
    if i >= len(levels):
        spent = time.time() - _SEQ["t0"]
        tail = ("；失败 " + ",".join(f"第{v}关" for v in _SEQ["fail"])) if _SEQ["fail"] else ""
        log(f"迷踪盘连刷 完成：成功 {len(_SEQ['ok'])} 关{tail}，总用时 {spent / 60:.1f} 分钟")
        return 0, False, "全部跑完，收尾退大厅"

    level = levels[i]
    log(f"== 连刷 {i + 1}/{len(levels)}：第 {level} 关 ==")
    return level, True, f"第 {level} 关"


def _seq_done(level: int, ok: bool, msg: str) -> None:
    (_SEQ["ok"] if ok else _SEQ["fail"]).append(level)
    _SEQ["i"] = _SEQ["i"] + 1
    log(f"   第 {level} 关 {'成功' if ok else '跳过'}：{msg}")


def set_level(context, level: int) -> bool:
    """把 `迷踪盘.点位` 的 level 改成当前关号。

    连刷模式下关卡号每轮都变，而 interface 的 pipeline_override 只在启动时定一次，
    所以必须运行期改。`Context.override_pipeline` 是官方给运行期改节点字段的接口
    （与 interface 里的 pipeline_override 同一套合并语义）。
    """
    try:
        ok = context.override_pipeline({
            "迷踪盘.点位": {
                "action": {
                    "type": "Custom",
                    "param": {
                        "custom_action": "迷踪盘点位",
                        "custom_action_param": {"level": int(level)},
                    },
                }
            }
        })
    except Exception:
        log("override_pipeline 异常：\n" + traceback.format_exc())
        return False
    if not ok:
        log(f"override_pipeline 失败：迷踪盘.点位 level={level}")
    return bool(ok)


def register() -> None:
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction

    @AgentServer.custom_action("迷踪盘选关")
    class PickMizongpan(CustomAction):
        def run(self, context, argv) -> bool:
            param = {}
            try:
                param = json.loads(argv.custom_action_param or "{}")
            except Exception:
                log("选关 param 解析失败：\n" + traceback.format_exc())

            levels = [int(v) for v in (param.get("levels") or [])]
            if levels:
                # 连刷模式。约定：**返回 True 就一定已经点进某一关**。
                # 某关在列表里找不到时，在本次 action 内直接跳到下一个 levels 项继续找，
                # 不让 pipeline 承担「跳过」这件事 —— `前往.on_error -> 选关` 那条链
                # 属于 on_error 回路，会被 Maafw 判成 error handling loop 直接终止任务。
                while True:
                    level, cont, msg = _seq_next(context, levels)
                    if not cont:
                        log("迷踪盘选关 连刷结束：返回 False → 收尾退大厅")
                        return CustomAction.RunResult(success=False)
                    set_level(context, level)
                    ok, msg2 = run_pick(context, level)
                    _seq_done(level, ok, msg2)
                    if ok:
                        log(f"迷踪盘选关 成功：{msg2}")
                        return CustomAction.RunResult(success=True)
                    log(f"迷踪盘选关 跳过第 {level} 关：{msg2}")

            # 单关模式
            level = 0
            try:
                level = int(param.get("level") or 0)
            except Exception:
                log("选关 level 解析失败：\n" + traceback.format_exc())
            try:
                ok, msg = run_pick(context, level)
            except Exception:
                msg = "迷踪盘选关异常：\n" + traceback.format_exc()
                log(msg)
                ok = False
            log(f"迷踪盘选关 {'成功' if ok else '失败'}：{msg}")
            return CustomAction.RunResult(success=bool(ok))


def main(argv: list[str]) -> int:
    log("=" * 60)
    if "--dry" in argv:
        i = argv.index("--dry")
        level = int(argv[i + 1]) if len(argv) > i + 1 else 1
        if level > 0:
            t = want_text(level)
            log(f"level={level} 目标文本 {t!r} 自检："
                f"命中自身={hit_target(t, level)} "
                f"误命中第{level + 30}关={hit_target(f'第{level + 30}关', level)} "
                f"误命中第1{level}关={hit_target(f'第1{level}关', level)}")
        return 0
    log("迷踪盘选关：只作为 Custom action 使用，命令行仅支持 --dry <level>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
