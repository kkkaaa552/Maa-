# -*- coding: utf-8 -*-
"""时效性检查：每轮任务跑完后，验证「接线 / 模板 / 目录卫生 / 新鲜度」四项是否还成立。

为什么需要它——这个工程里最容易悄悄烂掉的是四类东西，而它们的失效方式都是静默的：

  1. 接线断了：改了 interface.json 的 option 名字、删了 pipeline 节点，next/on_error
     还指着旧名字。运行时只报 `Task not found`，界面上看不出哪里错。
  2. 模板量不动：roi 比模板本身还小，或模板换了新图但 roi 没跟着改。判据永远 miss，
     表现是「任务跑完显示成功，实际一关没刷」。
  3. 目录被污染：pipeline/ 下留了 `*.json.bak-*`，MaaFramework 每轮加载会刷一堆 WRN。
  4. 改了没跑过：源文件 mtime 晚于最后一次真实运行，你以为在验新代码，其实跑的是旧的。

全部检查都是静态的（读文件、读 PNG 头、读日志），不连设备、不点击、不消耗资源。

用法：
    python tools\\check_freshness.py                    全量检查
    python tools\\check_freshness.py --log              附带 maafw.log 体检
    python tools\\check_freshness.py --deep             真加载 bundle 验证覆盖是否生效（约 1 分钟）
    python tools\\check_freshness.py --quiet            只打问题与结论
    python tools\\check_freshness.py --json             机器可读（CI 用）
    python tools\\check_freshness.py --root D:\\某工程   检查别的工程

退出码：0 = 通过（可以有 warn / note），1 = 有 error，2 = 脚本自身出错
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from collections import deque
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(os.environ.get("CHECK_ROOT") or Path(__file__).resolve().parents[1]).resolve()
PIPE_DIR = ROOT / "resource" / "pipeline"
IMG_DIR = ROOT / "resource" / "image"
IFACE = ROOT / "interface.json"

# MaaFramework 默认把截图短边缩到 720，所有 pipeline 坐标都在这个画布空间里
CANVAS = (1280, 720)

# 项目里从来不用、出现即笔误的识别/动作类型
# 取值来自 maafw 的 maa/define.py 的 AlgorithmEnum / ActionEnum
KNOWN_RECOG = {
    "TemplateMatch", "FeatureMatch", "ColorMatch", "OCR", "DirectHit", "Custom",
    "NeuralNetworkClassify", "NeuralNetworkDetect", "And", "Or",
}
KNOWN_ACT = {"Click", "Swipe", "Key", "InputText", "StartApp", "StopApp", "Custom", "DoNothing", "StopTask", "Command"}

# 脚本类误报：这些行出现在日志里属于正常噪声，不算问题
LOG_BENIGN = [
    re.compile(r"entry is not \*\.json or \*\.jsonc, skip"),          # pipeline 目录里有 .bak
    re.compile(r"ChildPipeIOStream.*release"),                        # 管道关闭时的固定噪声
]

items: list[dict] = []  # {"level": "ERR"|"WARN"|"NOTE", "id": str, "msg": str}


def add(level: str, cid: str, msg: str) -> None:
    items.append({"level": level, "id": cid, "msg": msg})


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def png_size(p: Path) -> tuple[int, int] | None:
    """直接读 PNG 的 IHDR，不依赖 Pillow —— 检查脚本要能脱离 .venv 跑。"""
    try:
        with p.open("rb") as f:
            head = f.read(33)
        if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        if head[12:16] != b"IHDR":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    except OSError:
        return None


# ============================================================ A. 接线

def collect_nodes() -> tuple[dict[str, dict], dict[str, str], list[str]]:
    """全库节点并集。MaaFramework 把所有 pipeline json 合并成一棵树，
    所以 00_common.json 的节点可以被别的文件 next 到，必须并起来查。"""
    nodes: dict[str, dict] = {}
    owner: dict[str, str] = {}
    dups: list[str] = []
    for p in sorted(PIPE_DIR.glob("*.json")):
        try:
            d = load_json(p)
        except Exception as e:  # noqa: BLE001
            add("ERR", "A0", f"{p.name} 解析失败：{type(e).__name__}: {e}")
            continue
        for name, body in d.items():
            if name.startswith("$"):
                continue
            if not isinstance(body, dict):
                add("ERR", "A0", f"{p.name}: 节点 {name!r} 的值不是对象（{type(body).__name__}）")
                continue
            if name in nodes:
                # 多文件重复顶层 key，MaaFramework 直接拒绝整个 bundle，而
                # Python 的 json.load 单文件查不出来，只能这样跨文件比对
                dups.append(f"{name}（{owner[name]} 与 {p.name}）")
                continue
            nodes[name] = body
            owner[name] = p.name
    for d in dups:
        add("ERR", "A0", f"跨文件重复节点名：{d}")
    return nodes, owner, dups


def check_edges(nodes: dict[str, dict], owner: dict[str, str]) -> None:
    for name, body in nodes.items():
        for field in ("next", "on_error", "interrupt"):
            tgts = body.get(field)
            if tgts is None:
                continue
            if not isinstance(tgts, list):
                add("ERR", "A1", f"[{owner[name]}] {name}.{field} 不是数组")
                continue
            for t in tgts:
                if not isinstance(t, str):
                    add("ERR", "A1", f"[{owner[name]}] {name}.{field} 里有非字符串目标 {t!r}")
                elif t.startswith("[") or t in ("stop",):
                    continue  # [JumpBack]xxx / [Anchor]xxx / 内置 stop
                elif t not in nodes:
                    add("ERR", "A1", f"[{owner[name]}] {name}.{field} → {t!r} 全库找不到")


def check_recog_action(nodes: dict[str, dict], owner: dict[str, str]) -> None:
    for name, body in nodes.items():
        r = body.get("recognition")
        if isinstance(r, str):
            if r not in KNOWN_RECOG:
                add("WARN", "A2", f"[{owner[name]}] {name}.recognition 简写 {r!r} 不在已知类型里")
            continue
        if r is None:
            continue
        if not isinstance(r, dict):
            add("ERR", "A2", f"[{owner[name]}] {name}.recognition 形态异常 {type(r).__name__}")
            continue
        rt = r.get("type")
        if rt is None:
            add("ERR", "A2", f"[{owner[name]}] {name}.recognition 缺 type")
        elif rt not in KNOWN_RECOG:
            # 拼错的 type（如 TemplateMacht）MaaFramework 认不出来，该节点的识别直接不成立；
            # 而这类错误在界面上只表现为「任务跑完没效果」，不会报错
            add("ERR", "A2",
                f"[{owner[name]}] {name}.recognition type={rt!r} 不是已知识别类型"
                f"（可能是拼写错误，该节点识别会失败）")

        a = body.get("action")
        if isinstance(a, str):
            if a not in KNOWN_ACT:
                add("WARN", "A2", f"[{owner[name]}] {name}.action 简写 {a!r} 不在已知类型里")
            continue
        if a is None:
            continue
        if not isinstance(a, dict):
            add("ERR", "A2", f"[{owner[name]}] {name}.action 形态异常 {type(a).__name__}")
            continue
        at = a.get("type")
        if at is None:
            add("ERR", "A2", f"[{owner[name]}] {name}.action 缺 type")
        elif at not in KNOWN_ACT:
            add("ERR", "A2",
                f"[{owner[name]}] {name}.action type={at!r} 不是已知动作类型"
                f"（可能是拼写错误，该节点动作会失败）")

        tg = (a.get("param") or {}).get("target")
        if isinstance(tg, list) and len(tg) not in (2, 4):
            # target 只写 3 个数字会让 MaaFramework 解析失败，而运行时日志仍报
            # 「0 pipelines failed」——典型的假阴性，必须在这里拦
            add("ERR", "A2", f"[{owner[name]}] {name}.action.target 长度 {len(tg)}（只允许 2 或 4）")

        for flat in ("roi", "template", "threshold", "target", "expected"):
            if flat in body:
                add("WARN", "A2", f"[{owner[name]}] {name} 顶层平铺 {flat}（V2 应放进 param）")


def reachable(nodes: dict[str, dict], entry: str) -> set[str]:
    if entry not in nodes:
        return set()
    seen, q = {entry}, deque([entry])
    while q:
        cur = q.popleft()
        body = nodes.get(cur) or {}
        for field in ("next", "on_error"):
            for t in body.get(field) or []:
                if isinstance(t, str) and t in nodes and t not in seen:
                    seen.add(t)
                    q.append(t)
    return seen


def check_interface(nodes: dict[str, dict]) -> dict:
    try:
        iface = load_json(IFACE)
    except Exception as e:  # noqa: BLE001
        add("ERR", "A0", f"interface.json 解析失败：{type(e).__name__}: {e}")
        return {}

    # --- 任务入口
    task_names: dict[str, dict] = {}
    for t in iface.get("task", []):
        task_names[t["name"]] = t
        entry = t.get("entry")
        if not entry:
            add("ERR", "A3", f"task {t['name']} 没有 entry")
        elif entry not in nodes:
            add("ERR", "A3", f"task {t['name']} 的 entry={entry!r} 在任何 pipeline 里都不存在")

    # --- option 与其覆盖目标
    override_targets: set[str] = set()
    for oname, opt in (iface.get("option") or {}).items():
        cases = opt.get("cases") or []
        if not cases:
            add("WARN", "A4", f"option {oname} 没有任何 case")
        dc = opt.get("default_case")
        case_names = {c["name"] for c in cases}
        if dc is not None and dc not in case_names:
            add("ERR", "A4", f"option {oname} 的 default_case={dc!r} 不在 case 列表里")
        if opt.get("type") == "switch":
            bad = case_names - {"Yes", "No"}
            if bad:
                add("ERR", "A4", f"option {oname} 是 switch，但 case 名 {sorted(bad)} 不是 Yes/No")
        for c in cases:
            for node_name, patch in (c.get("pipeline_override") or {}).items():
                if node_name not in nodes:
                    add("ERR", "A4", f"option {oname} case {c['name']}：覆盖目标节点 {node_name!r} 不存在")
                    continue
                # pipeline_override 是深合并，可以给节点新增原本没有的字段，
                # 所以「子键不存在」不算错。真正要抓的是：新增了 recognition 内容
                # 却没给 type —— type 缺省是 DirectHit，param 会被整个忽略，覆盖静默失效。
                base = nodes[node_name]
                pat_rec = patch.get("recognition")
                if isinstance(pat_rec, dict) and pat_rec.get("param"):
                    base_rec = base.get("recognition")
                    base_type = base_rec.get("type") if isinstance(base_rec, dict) else base_rec
                    # 缺省类型就是 DirectHit，而 DirectHit 不使用 param —— 所以
                    # 「原节点没有 recognition」与「原节点是 DirectHit」是同一件事。
                    uses_param = bool(base_type) and base_type != "DirectHit"
                    if not pat_rec.get("type") and not uses_param:
                        # 这条是本工程最阴的一类 bug：节点是 DirectHit，override 只挂了
                        # recognition.param 却没给 type。深合并后 type 仍是 DirectHit，
                        # param 被整个忽略 —— 任务照跑、日志全绿、判据实际没生效。
                        add("ERR", "A4",
                            f"option {oname} case {c['name']}：{node_name} 覆盖了 recognition.param "
                            f"但既没给 type，原节点又不是需要 param 的识别类型"
                            f"（{base_type or 'DirectHit'} 会忽略 param），该覆盖静默失效")
                for field in ("next", "on_error"):
                    for t in patch.get(field) or []:
                        if isinstance(t, str) and not t.startswith("["):
                            if t not in nodes:
                                add("ERR", "A4",
                                    f"option {oname} case {c['name']}：{node_name}.{field} 覆盖目标 {t!r} 不存在")
                            override_targets.add(t)

    # --- preset 与 option 取值
    for pre in iface.get("preset") or []:
        for item in pre.get("task") or []:
            tn = item.get("name")
            if tn not in task_names:
                add("ERR", "A5", f"preset {pre['name']} 引用了不存在的 task {tn!r}")
                continue
            declared = task_names[tn].get("option") or []
            for oname, val in (item.get("option") or {}).items():
                if oname not in declared:
                    add("ERR", "A5", f"preset {pre['name']}：task {tn} 没有声明 option {oname!r}")
                    continue
                cases = {c["name"] for c in (iface["option"][oname].get("cases") or [])}
                if val not in cases:
                    add("ERR", "A5", f"preset {pre['name']}：option {oname} 的值 {val!r} 不在 case 列表里")

    # --- 孤儿节点：既从 task entry 不可达，也不是任何 override 的目标
    entries = {t.get("entry") for t in iface.get("task", []) if t.get("entry")}
    seen: set[str] = set()
    for e in entries:
        seen |= reachable(nodes, e)
    for t in sorted(override_targets):
        if t in nodes:
            seen |= reachable(nodes, t)
    orphans = sorted(set(nodes) - seen)
    if orphans:
        # 孤儿不等于错误：00_smoke.json / 99_dump.json 这类调试节点本来就不挂任务。
        # 但新写的节点忘了接到 next 上，也会落在这里，所以要逐条看。
        add("NOTE", "A6", f"从任何 task entry 都不可达的节点 {len(orphans)} 个：{', '.join(orphans)}")

    return iface


# ============================================================ B. 模板

def check_templates(nodes: dict[str, dict], owner: dict[str, str], iface: dict) -> None:
    used: set[str] = set()          # pipeline 节点里直接引用的
    via_override: set[str] = set()  # 只在 interface option 覆盖里出现的
    cw, ch = CANVAS

    # 先把 override 里引用的模板收进来，这样「未被引用」的统计不会被它们污染
    for opt in (iface.get("option") or {}).values():
        for c in opt.get("cases") or []:
            for patch in (c.get("pipeline_override") or {}).values():
                pr = patch.get("recognition") if isinstance(patch, dict) else None
                if isinstance(pr, dict):
                    t = (pr.get("param") or {}).get("template")
                    for x in ([t] if isinstance(t, str) else (t or [])):
                        via_override.add(x)

    for name, body in nodes.items():
        r = body.get("recognition")
        if not isinstance(r, dict) or r.get("type") != "TemplateMatch":
            continue
        pa = r.get("param") or {}
        raw = pa.get("template")
        tpls = [raw] if isinstance(raw, str) else (raw or [])
        roi = pa.get("roi")
        thr = pa.get("threshold", 0.8)

        if not tpls:
            add("ERR", "B0", f"[{owner[name]}] {name} 是 TemplateMatch 但没写 template")
            continue

        for tn in tpls:
            used.add(tn)
            tp = IMG_DIR / tn
            if not tp.is_file():
                add("ERR", "B0", f"[{owner[name]}] {name} 的模板不存在：{tn}")
                continue
            size = png_size(tp)
            if size is None:
                add("WARN", "B0", f"[{owner[name]}] {name} 的模板读不出 PNG 尺寸：{tn}")
                continue
            tw, th = size

            if roi is None:
                # 不给 roi 就是全画面搜索，模板必须小于画布
                if tw > cw or th > ch:
                    add("ERR", "B2", f"[{owner[name]}] {name} 的模板 {tn}（{tw}x{th}）比画布还大且没写 roi")
                continue
            if not (isinstance(roi, list) and len(roi) == 4):
                add("ERR", "B1", f"[{owner[name]}] {name} 的 roi 不是 4 元素：{roi!r}")
                continue
            x, y, w, h = roi

            fits = w >= tw and h >= th
            if not fits:
                # roi 比模板小的时候 OpenCV 不报错，而是悄悄把 image 与 template 对调
                # （实测：模板 92x32 + roi 68x29 → result 25x4，正是 92-68+1 / 32-29+1），
                # 于是「模板在不在这块画面里」变成了「这块画面在不在模板里」，分数无意义。
                # 若该节点还有别的模板能装下，就只是这一个模板永不命中；全装不下则是整条判据失效。
                others = [t2 for t2 in tpls if t2 != tn]
                ok_other = False
                for t2 in others:
                    s2 = png_size(IMG_DIR / t2) if (IMG_DIR / t2).is_file() else None
                    if s2 and w >= s2[0] and h >= s2[1]:
                        ok_other = True
                        break
                lvl = "WARN" if ok_other else "ERR"
                tail = "（同节点其它模板能装下，仅此模板永不命中）" if ok_other else "，判据失效"
                add(lvl, "B1",
                    f"[{owner[name]}] {name} 的 roi {roi} 装不下模板 {tn}（{tw}x{th}）{tail}"
                    f"——OpenCV 会将 image/template 对调，分数不再代表命中")
                continue

            # roi 恰好等于模板尺寸是合法的「精确矩形」用法：matchTemplate 返回 1x1，
            # 等价于在固定位置比对一次。只有「大于模板但余量很小」才真的脆。
            mw, mh = w - tw, h - th
            if 0 < mw < 8 or 0 < mh < 8:
                add("WARN", "B3",
                    f"[{owner[name]}] {name} 的 roi 余量太小（w-模板宽={mw}, h-模板高={mh}），"
                    f"目标页轻微位移就掉出窗口")

        # roi 越界：Maa 会裁剪，但裁掉的部分等于白设。只要裁完还能容纳模板就只是浪费，
        # 裁到装不下模板就已经被上面那条拦住了，所以这里只报 WARN。
        if isinstance(roi, list) and len(roi) == 4 and all(isinstance(v, int) for v in roi):
            x, y, w, h = roi
            if x < 0 or y < 0:
                add("ERR", "B2", f"[{owner[name]}] {name} 的 roi 起点为负 {roi}")
            over_x = max(0, x + w - cw)
            over_y = max(0, y + h - ch)
            if over_x or over_y:
                add("WARN", "B2",
                    f"[{owner[name]}] {name} 的 roi {roi} 越出 {cw}x{ch} 画布"
                    f"（右溢 {over_x}px / 下溢 {over_y}px，会被裁掉）")

        if isinstance(thr, (int, float)):
            if thr <= 0 or thr >= 1:
                add("ERR", "B4", f"[{owner[name]}] {name} 的 threshold={thr}（应为 0<阈值<1）")
            elif thr < 0.6:
                add("WARN", "B4", f"[{owner[name]}] {name} 的 threshold={thr} 偏低，容易误命中")

    # --- 孤儿模板：目录里有图但没被任何节点或 option 覆盖引用
    # resource/image 下除了 pipeline 判据模板，还有一大批识别用的资源库图
    # （物品/ 171 张由 Python 侧读目录做名称比对，顶层也有一批）。所以这里只报 NOTE，
    # 真正的关注点是「顶层以 作战./征集./迷踪盘. 这类前缀命名的判据图」被删了却还有人引用——
    # 那个由 B0 的「模板不存在」兜住。这里只列出与节点同名却没被引用的可疑项。
    all_imgs = {
        str(p.relative_to(IMG_DIR)).replace("\\", "/")
        for p in IMG_DIR.rglob("*.png")
    }
    unused = sorted(all_imgs - used - via_override)
    node_names = set(nodes)
    suspicious = [
        u for u in unused
        if u.endswith(".png") and u[:-4] in node_names
    ]
    if suspicious:
        add("WARN", "B5",
            f"{len(suspicious)} 张图的文件名与某个节点同名但没被引用（改过模板名？）："
            + ", ".join(suspicious[:6]))
    if unused:
        add("NOTE", "B5", f"目录里有 {len(unused)} 个模板未被引用（多为资源库图，不是错误）")


# ============================================================ C. 目录卫生

def check_hygiene() -> None:
    junk = [p.name for p in PIPE_DIR.iterdir() if p.is_file() and p.suffix != ".json"]
    if junk:
        # MaaFramework 每轮 load_all_json 都会为每个非 .json 文件刷一条 WRN
        add("WARN", "C0",
            f"pipeline/ 下有 {len(junk)} 个非 .json 文件，每轮加载都会刷 WRN："
            + ", ".join(sorted(junk)[:6]) + ("…" if len(junk) > 6 else ""))

    for sub in ("agent", "tools", "resource/pipeline", "config"):
        d = ROOT / sub
        if not d.is_dir():
            continue
        baks = [p.name for p in d.iterdir() if p.is_file() and ".bak" in p.name]
        if baks:
            add("NOTE", "C1", f"{sub}/ 下有 {len(baks)} 个 .bak 备份")


# ============================================================ C2. 深度校验（真加载）

_JP_FIELDS = (
    "recognition", "action", "next", "on_error", "max_hit", "timeout",
    "pre_delay", "post_delay", "enabled", "repeat", "inverse",
)


def _node_fingerprint(res, name: str) -> str | None:
    """把节点编译后的全部关键字段拍成一个字符串。

    必须用 repr(recognition) / repr(action)，不能只看 .type ——
    action.param 里的 custom_action_param 变了而 type 不变，只看 type 会漏判。
    """
    o = res.get_node_object(name)
    if o is None:
        return None
    d = {}
    for f in _JP_FIELDS:
        v = getattr(o, f, None)
        if f in ("recognition", "action"):
            d[f] = repr(v)
        elif f in ("next", "on_error"):
            d[f] = [getattr(x, "name", x) for x in (v or [])]
        else:
            d[f] = v
    return repr(d)


def check_deep() -> None:
    """加载真实 bundle，对每条 option 覆盖「应用前/后」编译结果做差。

    这是唯一能证明覆盖真的生效的办法：静态读 JSON 只能看到 patch 里写了什么，
    看不到 MaaFramework 合并编译后到底留下了什么。实测过一个反例——
    `override_pipeline({"作战.选类别": {"recognition": {"param": {...}}}})` 返回 True，
    但节点编译后 recognition 仍是 DirectHit、param 是 JDirectHit，
    那条覆盖从编译期就被丢掉了，跑起来日志全绿却没有任何判据生效。
    """
    try:
        from maa.resource import Resource  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        add("WARN", "C2", f"跳过深度校验：import maa 失败（{type(e).__name__}: {e}），"
                          f"请用项目 .venv 里的 python 运行")
        return

    try:
        iface = load_json(IFACE)
    except Exception:  # noqa: BLE001
        add("WARN", "C2", "跳过深度校验：interface.json 读不出")
        return

    bundle = ROOT / "resource"
    res = Resource()
    job = res.post_bundle(str(bundle)).wait()
    if not getattr(res, "loaded", False):
        add("ERR", "C2", f"深度校验：bundle 加载失败（{job}）")
        return
    n_before = len(res.node_list)

    real = dead = redundant = 0
    dead_keys: set[str] = set()
    for oname, opt in (iface.get("option") or {}).items():
        for c in opt.get("cases") or []:
            for node, patch in (c.get("pipeline_override") or {}).items():
                if node not in res.node_list:
                    continue  # A4 已经报过「覆盖目标节点不存在」
                # 每条都要从干净状态重载：override 会污染整个 Resource 实例
                res.clear()
                res.post_bundle(str(bundle)).wait()
                before = _node_fingerprint(res, node)
                res.override_pipeline({node: patch})
                after = _node_fingerprint(res, node)
                if before != after:
                    real += 1
                    continue
                # 编译结果没变：要么 patch 的值本来就等于基线（冗余，无害），
                # 要么 patch 真的被丢掉了。要区分这两者。
                if _patch_equals_baseline(res, node, patch):
                    redundant += 1
                else:
                    dead += 1
                    dead_keys.add(f"option {oname} case {c['name']}：")
                    add("ERR", "C2",
                        f"option {oname} case {c['name']}：{node} 的覆盖 {sorted(patch)} "
                        f"编译后与基线完全相同——覆盖被丢弃，实际没生效")

    # A4 是静态推断（「按语义应该失效」），C2 是引擎实测（「确实没生效」）。
    # 两者命中同一批时只留 C2 那条 ERR，A4 降为 NOTE，免得同一条问题算两次 error。
    if dead_keys:
        for it in items:
            if it["id"] == "A4" and it["level"] == "ERR":
                if any(it["msg"].startswith(k) for k in dead_keys):
                    it["level"] = "NOTE"
                    it["id"] = "C2"
                    it["msg"] += "（已由深度校验证实）"

    add("NOTE", "C2",
        f"深度校验完成：覆盖 {real} 条生效 / {redundant} 条与基线同值（冗余无害）"
        f" / {dead} 条被丢弃；bundle 节点 {n_before} 个")


def _patch_equals_baseline(res, node: str, patch: dict) -> bool:
    """patch 里写的值是否和节点基线本来就一样。

    一样的东西覆盖上去自然看不出变化，那不叫失效。
    """
    o = res.get_node_object(node)
    if o is None:
        return False
    for k, v in patch.items():
        if k == "next" or k == "on_error":
            if [getattr(x, "name", x) for x in (getattr(o, k, None) or [])] != v:
                return False
        elif k == "max_hit":
            if getattr(o, "max_hit", None) != v:
                return False
        elif k in ("timeout", "pre_delay", "post_delay", "repeat", "enabled", "inverse"):
            if getattr(o, k, None) != v:
                return False
        elif k == "recognition":
            pr = v.get("param") or {}
            rt = str(getattr(o.recognition, "type", "")).split(".")[-1]
            if "template" in pr and rt != "TemplateMatch":
                return False
            if "threshold" in pr and rt == "TemplateMatch":
                if list(getattr(o.recognition.param, "threshold", []) or []) != [pr["threshold"]]:
                    return False
            if "roi" in pr and list(getattr(o.recognition.param, "roi", []) or []) != pr["roi"]:
                return False
        elif k == "action":
            ap = v.get("param") or {}
            if "custom_action_param" in ap:
                base = getattr(o.action.param, "custom_action_param", None)
                if base != ap["custom_action_param"]:
                    return False
            if "target" in ap and list(getattr(o.action.param, "target", []) or []) != ap["target"]:
                return False
        else:
            return False  # 不认识的键，保守判成「不是冗余」
    return True


# ============================================================ D. 新鲜度

def last_run_time() -> tuple[float | None, str]:
    """以 maafw.log 的 mtime 作为「最后一次真实运行」的基准。

    不用 runtime logs\\log-*.log：那个是 MFAAvalonia 的界面日志，开客户端就会写，
    不代表 MaaFramework 真的跑过任务。maafw.log 只有 tasker 起来才写。
    """
    cands = [
        ROOT / "config" / "debug" / "maafw.log",
        ROOT / "debug" / "maafw.log",
    ]
    best, who = None, ""
    for c in cands:
        if c.is_file():
            m = c.stat().st_mtime
            if best is None or m > best:
                best, who = m, str(c.relative_to(ROOT))
    return best, who


def check_freshness(iface: dict) -> None:
    import datetime

    last, who = last_run_time()
    sources: list[Path] = []
    if IFACE.is_file():
        sources.append(IFACE)
    sources += sorted(PIPE_DIR.glob("*.json"))
    dp = ROOT / "resource" / "default_pipeline.json"
    if dp.is_file():
        sources.append(dp)
    sources += sorted((ROOT / "agent").glob("*.py"))

    if last is None:
        add("NOTE", "D0", "找不到 maafw.log，无法判断「改了有没有跑过」")
        return

    fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S")  # noqa: E731
    newer = sorted(
        (p for p in sources if p.is_file() and p.stat().st_mtime > last),
        key=lambda p: p.stat().st_mtime,
    )
    if newer:
        # 这条是重点：改完没重启没跑，你以为在验新代码，其实跑的是上一版
        listing = ", ".join(f"{p.relative_to(ROOT)}" for p in newer[:8])
        add("WARN", "D1",
            f"{len(newer)} 个源文件晚于最后一次运行（{who} @ {fmt(last)}），改动尚未验证：{listing}"
            + ("…" if len(newer) > 8 else ""))
    else:
        add("NOTE", "D1", f"所有源文件都不晚于最后一次运行（{who} @ {fmt(last)}）")

    if iface:
        version = iface.get("version")
        add("NOTE", "D2", f"interface.json version = {version}")


# ============================================================ E. 日志体检

LOG_PATTERNS = [
    ("加载失败", re.compile(r"open_and_parse_file failed|load_bundle\] .*\[ret=false\]")),
    ("节点引用错误", re.compile(r"node_reference_not_found|invalid_node|duplicate_node|empty_node_name")),
    ("路径缺失", re.compile(r"path_not_found|json_read_failed|invalid_pipeline_root")),
    ("任务超时", re.compile(r"Task timeout|TaskTimeout")),
    ("入口不存在", re.compile(r"task entry not found|Task not found")),
    ("adb 子进程报错", re.compile(r"child return error")),
]


def check_log(tail_mb: int = 4) -> None:
    log = ROOT / "config" / "debug" / "maafw.log"
    if not log.is_file():
        log = ROOT / "debug" / "maafw.log"
    if not log.is_file():
        add("NOTE", "E0", "找不到 maafw.log，跳过日志体检")
        return

    size = log.stat().st_size
    try:
        with log.open("rb") as f:
            if size > tail_mb * 1024 * 1024:
                f.seek(size - tail_mb * 1024 * 1024)
                f.readline()  # 丢掉被截断的半行
            raw = f.read()
    except PermissionError:
        # 客户端在跑时日志被独占，Python 拿不到共享读句柄
        add("NOTE", "E0", "maafw.log 被占用（客户端正在运行），跳过日志体检")
        return

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    add("NOTE", "E0", f"日志体检范围：最后 {tail_mb} MB / {len(lines)} 行")

    for label, pat in LOG_PATTERNS:
        hits = [ln for ln in lines if pat.search(ln)]
        if hits:
            # 逐条打印会刷屏，只留最后一条做证据
            sample = hits[-1][:200]
            add("ERR", "E1", f"{label} {len(hits)} 次，末例：{sample}")

    err_lines = [ln for ln in lines if "][ERR]" in ln]
    benign = [ln for ln in err_lines if any(p.search(ln) for p in LOG_BENIGN)]
    real = [ln for ln in err_lines if ln not in benign]
    if real:
        add("WARN", "E2", f"ERR 级别日志 {len(real)} 行，末例：{real[-1][:200]}")


# ============================================================ 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_freshness.py", description="项目时效性检查")
    ap.add_argument("--log", action="store_true", help="附带 maafw.log 体检")
    ap.add_argument("--deep", action="store_true",
                    help="深度校验：真加载 bundle，验证每条 option 覆盖是否生效（约 1 分钟）")
    ap.add_argument("--tail-mb", type=int, default=4, help="日志体检只读最后 N MB（默认 4）")
    ap.add_argument("--quiet", action="store_true", help="只打 WARN/ERR 与结论")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--root", default=None,
                    help="要检查的工程根目录（默认本脚本的上级目录；也可用环境变量 CHECK_ROOT）")
    args = ap.parse_args(argv)

    if args.root:
        global ROOT, PIPE_DIR, IMG_DIR, IFACE
        ROOT = Path(args.root).resolve()
        PIPE_DIR = ROOT / "resource" / "pipeline"
        IMG_DIR = ROOT / "resource" / "image"
        IFACE = ROOT / "interface.json"

    self_error = False
    try:
        nodes, owner, _dups = collect_nodes()
        check_edges(nodes, owner)
        check_recog_action(nodes, owner)
        iface = check_interface(nodes)
        check_templates(nodes, owner, iface)
        check_hygiene()
        if args.deep:
            check_deep()
        check_freshness(iface)
        if args.log:
            check_log(args.tail_mb)
    except Exception as e:  # noqa: BLE001
        # 检查脚本自己崩了，和「检查出问题」不是一回事，退出码要分开，
        # 否则 CI 里分不清是该修项目还是该修脚本
        self_error = True
        add("ERR", "Z0", f"检查脚本自身出错：{type(e).__name__}: {e}")

    n_err = sum(1 for i in items if i["level"] == "ERR")
    n_warn = sum(1 for i in items if i["level"] == "WARN")
    n_note = sum(1 for i in items if i["level"] == "NOTE")
    n_nodes = locals().get("nodes") or {}

    # 退出码：0 通过（可以有 warn/note）、1 有 error、2 脚本自身出错
    code = 2 if self_error else (0 if n_err == 0 else 1)

    if args.json:
        print(json.dumps({
            "root": str(ROOT),
            "nodes": len(n_nodes),
            "ok": n_err == 0 and not self_error,
            "self_error": self_error,
            "error": n_err, "warn": n_warn, "note": n_note,
            "items": items,
        }, ensure_ascii=False, indent=2))
        return code

    print("=" * 72)
    print(" 时效性检查  " + str(ROOT))
    print("=" * 72)
    print(f" 节点 {len(n_nodes)} 个  |  ERR {n_err}  WARN {n_warn}  NOTE {n_note}")
    print()

    groups = [
        ("A  接线", ("A0", "A1", "A2", "A3", "A4", "A5", "A6")),
        ("B  模板", ("B0", "B1", "B2", "B3", "B4", "B5")),
        ("C  目录卫生", ("C0", "C1", "C2")),
        ("D  新鲜度", ("D0", "D1", "D2")),
        ("E  日志", ("E0", "E1", "E2")),
        ("Z  脚本", ("Z0",)),
    ]
    mark = {"ERR": "✗", "WARN": "!", "NOTE": "·"}
    for title, ids in groups:
        rows = [i for i in items if i["id"] in ids]
        if args.quiet:
            rows = [i for i in rows if i["level"] != "NOTE"]
        if not rows:
            continue
        print(f"--- {title} ---")
        for i in rows:
            print(f"  {mark[i['level']]} {i['msg']}")
        print()

    if n_err:
        print(f"结论：有 {n_err} 个 error，先修再跑任务。")
        return code
    if n_warn:
        print(f"结论：没有 error，{n_warn} 条 warn 值得看一眼。")
        return code
    print("结论：全部通过。")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
