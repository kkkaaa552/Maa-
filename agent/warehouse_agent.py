"""物华弥新 MAA · Agent 侧自定义动作

被 MFAAvalonia 用 `child_exec` 拉起（见 interface.json 的 agent 段），
identifier 由 Client 作为第一个命令行参数传入。

两个自定义动作：
- 仓库抓取：逐页截图 → wh_scan 识别 → 合并进 data/warehouse/*.json
- 仓库快照：把 data/warehouse/*.json 渲染成 report/仓库快照.md（面板任务说明用）

出口只有 Markdown 一份（用户 2026-11-22 定：可浏览的 HTML 图墙不做）。

本地手工验证（不需要 Client）：
    .venv\\Scripts\\python.exe agent\\warehouse_agent.py --selftest
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "report"
LOG_PATH = ROOT / "debug" / "wh_agent.log"

# 打包后跑的是嵌入式 Python（python313._pth 让解释器进入 isolated 模式），
# **脚本所在目录不会自动进 sys.path** —— 实测 sys.path[0] 是
# `python\python313.zip`，`import combat_balance` 直接 ModuleNotFoundError，
# 于是作战均衡与迷踪盘两个 Custom 动作静默注册失败（只在 maafw.log 里留一行
# Traceback，界面上看不出来）。所以本目录必须自己塞进去，不能依赖默认行为。
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
sys.path.insert(0, str(ROOT / "tools"))

# 原生库统一走 runtimes/win-x64/native —— 与 MFAAvalonia 界面**共用同一份**。
# 发布包里原本有两套 MaaFramework：runtimes\...\native（5.14.3，界面用）和
# python\Lib\site-packages\maa\bin（5.16.1，agent 用），同体积 86 MB 各一份。
# `maa/__init__.py` 只看两处：环境变量 MAAFW_BINARY_PATH，或包内 maa\bin；
# 它**不会**自动去找 runtimes。所以这里必须显式指路，否则一旦 maa\bin 里的
# dll 被精简掉，import maa 立刻 FileNotFoundError。
# 打包版实际只剩 5.16.1 一套（tools/build.py 的 step_ui 会用 maa/bin 覆盖
# runtimes/native，再把 maa/bin 的 dll 清空），两边版本号因此一致。
_NATIVE = ROOT / "runtimes" / "win-x64" / "native"
if (_NATIVE / "MaaFramework.dll").is_file():
    _given = os.environ.get("MAAFW_BINARY_PATH")
    # 外部显式设了有效路径就尊重它（开发机可能指向 maafw/）；没设、或设成了
    # 不存在的目录，就指向包里这套 runtimes。
    if not _given or not Path(_given).is_dir():
        os.environ["MAAFW_BINARY_PATH"] = str(_NATIVE)


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def parse_param(raw) -> dict:
    """把 custom_action_param 解析成 dict。

    坑（2026-11-22 13:25 实跑踩到）：MaaFramework 传给 agent 的不是 pipeline 里那个
    字符串本身，而是它的 JSON 编码 —— 字符串参数会多包一层引号（`"{\\"cat\\": \\"物料\\"}"`），
    没有参数时会传 `null` 字符串。所以只解一次 json 会得到 str 或 None，
    后面 `param.get` 直接 AttributeError，任务就「切到物料页 → 抓取失败 → 跳 on_error 回主页」。
    这里循环解到 dict 为止（最多 3 层）。
    """
    v = raw
    for _ in range(5):
        if isinstance(v, dict):
            return v
        if v is None:
            return {}
        if isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8", "replace")
            continue
        if isinstance(v, str):
            t = v.strip()
            if not t:
                return {}
            try:
                v = json.loads(t)
            except Exception:
                log(f"参数不是合法 JSON，按空参处理：{raw!r}")
                return {}
            continue
        return {}
    return {}


def build_markdown(docs: list[dict]) -> str:
    """把识别结果写成 Markdown，供任务说明面板显示。

    用户 2026-11-22 定：面板里只要「分类 + 图标 + 名字 + 数量 + 等级」，不要格数/种数/时间，
    也不要路径行 —— 所以这里只返回网格，什么都不追加。
    """
    import wh_report

    return wh_report.render_md_grid(docs, wh_report.icon_index(),
                                    wh_report.catalog_index(), wh_report.alias_index())


CAPTURE_FAILS: list[str] = []    # 本轮抓取失败记录：任何一页失败，最后的快照就必须判失败
LAST_SHOT: dict = {}             # 每个分类上一屏的截图（np 数组），第二屏合并时用来量位移


def make_report(param: dict) -> tuple[bool, str]:
    """只生成 Markdown 面板（report/仓库快照.md）。返回 (是否成功, 说明)。"""
    import wh_report  # tools/wh_report.py

    param = parse_param(param)               # 无参调用时 param 可能是 None
    if CAPTURE_FAILS:
        # 有一页没抓到：此时 data/warehouse 里是上一轮的旧数据，
        # 再生成一份「看起来正常」的报告就是在骗人，必须让任务以失败结束。
        return False, "抓取失败：" + "；".join(CAPTURE_FAILS)
    docs = wh_report.load(wh_report.CATS)
    if not docs:
        return False, "data/warehouse 下没有可用的仓库数据（先跑一次仓库识别）"

    md = build_markdown(docs)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REPORT_DIR / "仓库快照.md"
    md_path.write_text(md, encoding="utf-8")

    # 卡片计数口径：主表卡片形如 `<br>**名字**`，待定卡形如 `<br>⚠ 名字`
    cards = md.count("<br>**") + md.count("<br>⚠ ")
    msg = f"面板 {md_path.name} {md_path.stat().st_size:,} B · {cards} 张卡片"
    log(msg)
    return True, msg


def _screencap(context, cat: str, tag: str = ""):
    """抓一屏存到 debug/shots/ 并返回 (BGR 数组, 落盘文件名)（失败返回 (None, "")）。

    坑（2026-11-22 17:43 实跑踩到）：`post_screencap().wait().get()` 返回的是
    numpy.ndarray（BGR 图），不是 PNG 字节流。按字节流 `np.frombuffer(...)` +
    `cv2.imdecode` 去解会得到 None，日志表现为「第一屏没抓到」。

    返回真实文件名（不是 live-1 这种代号）就是为了 wh_report.crop_thumb：
    它拿 shot 去 debug/shots/ 拼路径裁缩略图，代号拼出来必然不存在，
    「名字待定」那几张卡就只剩一个点。见 2026-11-24 14:54 的修复。
    """
    import cv2

    try:
        img = context.tasker.controller.post_screencap().wait().get()
    except Exception:
        log("截图失败：\n" + traceback.format_exc())
        return None, ""
    if img is None:
        log("截图返回空")
        return None, ""
    shot_dir = ROOT / "debug" / "shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    shot = shot_dir / f"wh-live-{cat}{tag}-{datetime.now():%Y%m%d-%H%M%S}.png"
    try:
        ok, buf = cv2.imencode(".png", img)
        if ok:
            buf.tofile(str(shot))
    except Exception:
        log("截图落盘失败：\n" + traceback.format_exc())
        return img, ""
    return img, shot.name


def capture(param, context) -> tuple[bool, str]:
    """抓一个分类页的全部内容（慢滑翻到底，按位移合并）。"""
    import time

    import wh_scan as W

    param = parse_param(param)
    cat = param.get("cat") or "物料"
    rows = int(param.get("rows") or 4)
    max_scroll = int(param.get("scroll") or 0)
    if cat == "物料":
        # 物料是第一页：新一轮开始，清掉上一轮的失败记录。
        # 不清的话上一轮的失败会一直挂着，后面每一屏都判失败（17:44 那次就是这样堆了两条）。
        CAPTURE_FAILS.clear()
    out = ROOT / "data" / "warehouse" / f"{cat}.json"
    # 抓取过程中 merge_into 会一屏一屏往 out 里写。中途崩了就会留下半截数据
    # （2026-11-22 17:54 那次就留下 42 格的物料 JSON，正常是 60 格）。
    # 所以先留一份原样，失败时还原，绝不让半截数据被报告拿去用。
    keep = ROOT / "debug" / "_wh_last_good"
    keep.mkdir(parents=True, exist_ok=True)
    keep_file = keep / f"{cat}.json"
    had_old = out.exists()
    if had_old:
        shutil.copy2(out, keep_file)

    def _restore():
        if had_old and keep_file.exists():
            shutil.copy2(keep_file, out)
            log(f"{cat} 已还原抓取前的 {out.name}（丢弃半截数据）")
        elif not had_old and out.exists():
            out.unlink()
            log(f"{cat} 删掉了没写完的 {out.name}")

    img, shot_name = _screencap(context, cat)
    if img is None:
        msg = f"{cat} 第一屏没抓到"
        CAPTURE_FAILS.append(msg)
        return False, msg

    ref_phase = W.row_phase(img)[0]
    doc = W.scan_image(img, cat, rows, shot_name, out, verbose=False)
    prev = img
    cum_px = 0
    try:
        for i in range(max_scroll):
            # controller 每次都要重新取：Tasker.controller 是「现取一个 handle 包装」的
            # property，缓存在变量里跨多屏用会拿到失效句柄，
            # 17:54 那次就是 `access violation reading 0xA0` 崩在这儿。
            ctl = context.tasker.controller
            ctl.post_swipe(*W.SCROLL_BEGIN, *W.SCROLL_END, W.SCROLL_MS).wait()
            time.sleep(1.0)
            nxt, nxt_name = _screencap(context, cat, f"-s{i + 2}")
            if nxt is None:
                _restore()
                msg = f"{cat} 第{i + 2}屏没抓到"
                CAPTURE_FAILS.append(msg)
                return False, msg
            shift, match, sim = W.align(prev, nxt)
            # 先判「是不是同一页在滚动」，再判「到底了没」——顺序与 _rebuild_from_shots.py 一致，
            # 免得页面被误切走时因为位移小就当成到底了。
            if sim < W.SAME_PAGE_MIN:
                _restore()
                msg = (f"{cat} 第{i + 2}屏对不齐（位移 {shift}px、横带 {match:.2f}、"
                       f"整片相似 {sim:.3f} < {W.SAME_PAGE_MIN}），不是同一页在滚动")
                CAPTURE_FAILS.append(msg)
                log(msg)
                return False, msg
            if abs(shift) < W.BOTTOM_PX:
                log(f"{cat} 已经到底（位移 {shift}px < {W.BOTTOM_PX}px），停止翻转")
                break
            cum_px += shift
            phi = W.row_phase(nxt)[0]
            dy = W.phase_dy(ref_phase, phi)
            rows_offset = int(round((cum_px - dy) / W.PY))
            if rows_offset >= rows * (i + 1):
                # 翻过头：两屏可能不重叠，硬并会把整行数据错位，宁可整页判失败。
                _restore()
                msg = (f"{cat} 第{i + 2}屏翻过头了：累计位移 {cum_px}px ≈ {rows_offset} 行，"
                       f"两屏可能不重叠，拒绝合并（把拖拽距离调短或把 SCROLL_MS 调大）")
                CAPTURE_FAILS.append(msg)
                log(msg)
                return False, msg
            feed = nxt if dy == 0 else W.shift_image(nxt, dy)
            d2 = W.scan_image(feed, cat, rows, nxt_name, None,
                              verbose=False, row_offset=rows_offset,
                              shot_dy=dy)
            doc = W.merge_into(out, d2, nxt, prev, pre=(shift, match, sim),
                               offset_rows=rows_offset)
            log(f"{cat} 第{i + 2}屏：位移 {shift}px（横带 {match:.2f}、整片 {sim:.3f}）"
                f"· 相位 {phi} → 补偿 {dy:+d}px · 累计 {cum_px}px = {rows_offset} 行"
                f" → 累计 {len(doc.get('cells') or [])} 格")
            for c in (doc.get("meta") or {}).get("conflicts") or []:
                log(f"⚠ {cat} 第{c['row'] + 1}行第{c['col'] + 1}个 两屏名字不一致："
                    f"{c['prev']} vs {c['now']}")
            prev = nxt
    except Exception:
        # 任何异常（含 maa 底层 OSError/access violation）都要把半截 JSON 还原掉，
        # 否则下一屏没写完的数据会被报告当真。
        _restore()
        raise

    cells = doc.get("cells") or []
    LAST_SHOT[cat] = prev
    stat = {s: sum(1 for c in cells if c.get("status") == s)
            for s in ("hit", "uncertain", "empty")}
    msg = f"{cat} 抓到 {len(cells)} 格 {stat}"
    log(msg)
    return True, msg


def register() -> None:
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction

    @AgentServer.custom_action("仓库抓取")
    class CaptureWarehouse(CustomAction):
        def run(self, context, argv) -> bool:
            try:
                ok, msg = capture(argv.custom_action_param, context)
            except Exception:
                msg = "仓库抓取异常：\n" + traceback.format_exc()
                CAPTURE_FAILS.append(f"{msg.splitlines()[-1]}")
                log(msg)
                ok = False
            log(f"仓库抓取 {'成功' if ok else '失败'}：{msg.splitlines()[0] if msg else ''}")
            return CustomAction.RunResult(success=bool(ok))

    @AgentServer.custom_action("仓库快照")
    class SnapshotWarehouse(CustomAction):
        def run(self, context, argv) -> bool:
            try:
                ok, msg = make_report(argv.custom_action_param)
            except Exception:
                msg = "仓库快照异常：\n" + traceback.format_exc()
                log(msg)
                ok = False
            log(f"仓库快照 {'成功' if ok else '失败'}：{msg.splitlines()[0] if msg else ''}")
            return CustomAction.RunResult(success=bool(ok))

    # ── 作战刷取：均衡材料决策 ──────────────────────────────────────
    # interface.json 的 agent.child_args 只有这一个文件，所以「作战均衡决策」
    # 必须在同一个进程里注册，否则 pipeline 走到 作战.进入入口 / 作战.均衡决策
    # 这两个 Custom 节点会因动作名不存在而失败。
    try:
        import combat_balance
        combat_balance.register()
        log("已注册 custom action：作战均衡决策")
    except Exception:
        log("作战均衡决策 注册失败：\n" + traceback.format_exc())

    # ── 迷踪盘：读盘 + BFS 闭环求解 ────────────────────────────────
    # 同样必须在同一进程注册（agent.child_args 只有本文件）。
    try:
        import mizongpan
        mizongpan.register()
        log("已注册 custom action：迷踪盘求解")
    except Exception:
        log("迷踪盘求解 注册失败：\n" + traceback.format_exc())


def code_fingerprint() -> str:
    parts = []
    for rel in ("tools/wh_scan.py", "tools/wh_report.py", "agent/warehouse_agent.py"):
        p = ROOT / rel
        if not p.exists():
            continue
        st = p.stat()
        h = hashlib.sha256(p.read_bytes()).hexdigest()[:8]
        parts.append(f"{p.name}: {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}"
                     f" · {st.st_size:,} B · sha {h}")
    return "代码 " + " | ".join(parts)


def main(argv: list[str]) -> int:
    log("=" * 70)
    log(code_fingerprint())
    if "--selftest" in argv:
        ok, msg = make_report({})
        log(f"[selftest] {'OK' if ok else 'FAIL'} {msg}")
        return 0 if ok else 1

    from maa.agent.agent_server import AgentServer

    identifier = argv[1] if len(argv) > 1 else "warehouse"
    register()
    log(f"agent 启动，identifier = {identifier}")
    AgentServer.start_up(identifier)
    AgentServer.join()
    AgentServer.shut_down()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
