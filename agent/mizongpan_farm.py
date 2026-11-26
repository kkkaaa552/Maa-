# -*- coding: utf-8 -*-
"""迷踪盘连刷 Custom action：进关一次，按关卡区间连续刷完，最后统一退回大厅。

为什么要有这个（2026-11-26 用户提问）：
    原流程每关都是「点位 → 完成 → 收尾（退关卡页→退茶室→退派遣→大厅）」，
    刷 30 关就要走 30 遍「大厅→派遣公司→茶室→迷→关卡选择」四级导航，
    每遍多花 20 秒左右且每多一次导航就多一次失败面。
    实际通关点完「完成」之后人已经站在**关卡选择页**上了（实测：完成 → 收尾第一候选
    `迷踪盘.收尾.退关卡页` 直接命中 `迷踪盘.关卡选择.png`），所以在关卡页直接
    「选下一关 → 前往 → 点位 → 完成」可以整段留在关内，只有全部刷完才退出去。

为什么不做成纯 pipeline 循环：
    MaaFramework 的 `pipeline_override` 只能在**启动时**定一次，关卡号没法每轮自增；
    而上一轮实测把「找不到就翻页」写成 `选关 -on_error-> 翻页 -next-> 选关` 之后，
    Maafw 直接把这条 on_error 回路判成 `error handling loop detected` 终止任务
    （22.6s，succeeded=False）。所以「每轮换一个关卡号」的状态只能放在 Python 里。

节点接线（`resource/pipeline/迷踪盘.json`）：
    迷踪盘.关卡页 -next-> 迷踪盘.连刷 -next-> 迷踪盘.连刷.下一关 -next-> 迷踪盘.连刷
    迷踪盘.连刷 -on_error-> 迷踪盘.收尾
    即：每关一个节点转换（日志里能看见进度），跑完最后一关返回 False 落收尾退大厅。
    注意这是 **next 回路**，不是 on_error 回路 —— 前者是 Maafw 支持的正常循环写法
    （现网 `迷踪盘.点店.next = ["迷踪盘"]` 就是这种），后者会被环路检测掐掉。

失败策略：
    单关失败**不中断整串**，记进失败清单后继续下一关（连刷最怕一关崩掉整夜白跑）。
    只有在「连关卡页都回不去」这种无法继续的状态下才返回 False 提前收尾。
    最后一关结束时把「成功 N 关 / 失败 M 关」写进日志。
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "debug" / "mt_pts.log"
sys.path.insert(0, str(ROOT / "tools"))

# 模板判据一律用 1 - TM_SQDIFF_NORMED 口径复核过（本机 cv2 5.1.0 的
# TM_CCOEFF_NORMED 算错：随机图自匹配得 -0.068，详见 pipeline 节点 desc）。
TPL = {
    "关卡页": ("迷踪盘.关卡选择.png", (150, 160, 240, 70), 0.80),
    "前往": ("迷踪盘.前往.png", (700, 460, 380, 130), 0.80),
    "完成": ("迷踪盘.完成.png", (650, 320, 420, 220), 0.70),
    "弹窗": ("迷踪盘.弹窗确认.png", (660, 380, 220, 100), 0.80),
    "棋盘": ("迷踪盘.棋盘.png", (440, 540, 130, 110), 0.80),
}

ENTER_WAIT = 12.0     # 等「前往」/「完成」模板出现的最长秒数
PAGE_WAIT = 12.0      # 点完「完成」后等回到关卡页的最长秒数
POLL = 0.35
AFTER_CLICK = 1.5

_STATE = {"key": None, "levels": [], "i": 0, "ok": [], "fail": [], "t0": 0.0}


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ctl_of(context):
    """每次都从 context 现取 controller，**不要跨调用缓存**。

    2026-11-26 15:35 实测：在 Custom action 里先 `ctl = context.tasker.controller`
    存着，再去调另一个 Custom action（选关），回来继续用这个 ctl 截图，主进程报
        [ERR][AgentClient.cpp][L2799] query_controller  controller not found [controller_id=]
        [ERR][AgentClient.cpp][L2128] handle_controller_post_screencap  controller not found
    请求里的 controller_id 是空串 —— agent 侧的 Controller 代理句柄已经失效，任务就
    此卡死（日志停在「已点击第4关」，maafw.log 只剩 get_display_id 心跳）。
    现取现用可绕开；`mizongpan_pts.run_points` 那种「一口气跑完再返回」的用法本来
    就不跨节点，所以 26 关都正常。
    """
    return context.tasker.controller


def grab(context):
    """截一帧。失败/空图返回 None，由调用方决定重试节奏。"""
    for _ in range(2):
        try:
            img = ctl_of(context).post_screencap().wait().get()
        except Exception:
            img = None
        if img is not None:
            return img
        time.sleep(0.2)
    return None


_TPL_CACHE: dict = {}


def _tpl(name: str):
    """按名字取模板图（带缓存）。中文路径必须 np.fromfile + imdecode，cv2.imread 返回 None。"""
    if name in _TPL_CACHE:
        return _TPL_CACHE[name]
    import cv2
    import numpy as np

    p = ROOT / "resource" / "image" / name
    t = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR) if p.exists() else None
    _TPL_CACHE[name] = t
    return t


def find(context, img, name: str):
    """在模板自己的 roi 里做本地模板匹配，返回 (l, t, w, h) 或 None。

    **不用 `context.run_recognition_direct` 做 TemplateMatch**：2026-11-26 15:31
    实测它在本 Custom action 线程里会卡死任务队列（日志停在「已点击第4关」之后，
    `maafw.log` 只剩 `get_display_id` 心跳）。OCR 走那个接口是通的（选关用它），
    TemplateMatch 不通，所以模板一律本地 cv2 算。

    度量用 `1 - TM_SQDIFF_NORMED`：本机 cv2 5.1.0 的 TM_CCOEFF_NORMED 算错
    （随机图自匹配得 -0.068），TM_SQDIFF_NORMED 自匹配 min=0.000000 @ 正确位置。
    """
    import cv2

    tpl_name, roi, thr = TPL[name]
    t = _tpl(tpl_name)
    if t is None or img is None:
        return None
    x, y, w, h = roi
    sub = img[y:y + h, x:x + w]
    if sub.shape[0] < t.shape[0] or sub.shape[1] < t.shape[1]:
        return None
    r = cv2.matchTemplate(sub, t, cv2.TM_SQDIFF_NORMED)
    mn, _mx, ml, _mr = cv2.minMaxLoc(r)
    if 1.0 - float(mn) < thr:
        return None
    return (int(x + ml[0]), int(y + ml[1]), int(t.shape[1]), int(t.shape[0]))


def wait_tpl(context, name: str, timeout: float = ENTER_WAIT):
    t0 = time.time()
    while time.time() - t0 < timeout:
        box = find(context, grab(context), name)
        if box is not None:
            return box
        time.sleep(POLL)
    return None


def click_box(context, box, wait: float = AFTER_CLICK) -> tuple:
    x, y = box[0] + box[2] // 2, box[1] + box[3] // 2
    ctl_of(context).post_click(int(x), int(y)).wait()
    time.sleep(wait)
    return x, y


def on_level_page(context) -> bool:
    return find(context, grab(context), "关卡页") is not None


def ensure_level_page(context, tries: int = 6) -> bool:
    """把画面弄回关卡选择页。成功返回 True。

    退路顺序与收尾链路同源：先点「完成」遮罩（通关后常见的挡板），再点左上返回箭头
    (40,35)（棋盘页/茶室页的返回），每步都在下一轮重新判定，不做盲点连击。
    """
    if on_level_page(context):
        return True
    for i in range(1, tries + 1):
        img = grab(context)
        if img is None:
            time.sleep(POLL)
            continue
        box = find(context, img, "完成")
        if box is not None:
            click_box(context, box)
        else:
            box = find(context, img, "弹窗")
            if box is not None:
                click_box(context, box)
            else:
                ctl_of(context).post_click(40, 35).wait()
                time.sleep(1.6)
        if on_level_page(context):
            log(f"    已回到关卡页（第 {i} 次尝试）")
            return True
    return on_level_page(context)


def play_one(context, level: int) -> tuple:
    """在关卡页选中 level → 前往 → 点位/求解 → 完成。返回 (ok, msg)。"""
    import mizongpan as mz
    import mizongpan_pick as pick
    import mizongpan_pts as pts

    if not ensure_level_page(context):
        return False, "回不到关卡选择页"

    ok, msg = pick.run_pick(context, level)
    if not ok:
        return False, f"选关失败：{msg}"

    box = wait_tpl(context, "前往")
    if box is None:
        return False, "选中后等不到「前往」按钮"
    x, y = click_box(context, box, wait=1.7)
    log(f"    已点「前往」({x},{y})")

    try:
        moves = pts.load_points(level)
    except KeyError:
        return False, f"点位表里没有第 {level} 关"

    # pts/mz 的签名是 (ctl, ...) 且内部不跨节点，这里现取现用即可。
    ctl = ctl_of(context)
    if moves:
        ok, msg = pts.run_points(ctl, level, moves=moves)
    else:
        log(f"    第 {level} 关点位为空（攻略未标注）→ 交求解器")
        ok, msg = mz.solve_once(ctl)
    if not ok:
        return False, msg

    box = wait_tpl(context, "完成", timeout=8.0)
    if box is not None:
        x, y = click_box(context, box)
        log(f"    已点「完成」({x},{y})")
    else:
        log("    通关遮罩已过、未匹配到「完成」模板，直接进下一关判定")
    if not ensure_level_page(context):
        return False, "通关后回不到关卡选择页"
    return True, msg


def _init_state(param: dict) -> None:
    levels = [int(v) for v in (param.get("levels") or [])]
    key = tuple(levels)
    if _STATE["key"] != key:
        _STATE.update(key=key, levels=levels, i=0, ok=[], fail=[], t0=time.time())
        log(f"迷踪盘连刷 启动：{len(levels)} 关 {levels[0]}→{levels[-1]}" if levels
            else "迷踪盘连刷 启动：关卡列表为空")


def run_farm(context, param: dict) -> tuple:
    """跑一关。返回 (continue_, msg)。continue_=False 时由 pipeline 走 on_error 收尾。"""
    _init_state(param)
    levels = _STATE["levels"]
    i = _STATE["i"]

    if not levels:
        return False, "关卡列表为空"
    if i >= len(levels):
        spent = time.time() - _STATE["t0"]
        tail = ("；失败 " + ",".join(f"第{v}关" for v in _STATE["fail"])) if _STATE["fail"] else ""
        return False, (f"全部跑完：成功 {len(_STATE['ok'])} 关"
                       f"{tail}，总用时 {spent / 60:.1f} 分钟")

    level = levels[i]
    t0 = time.time()
    log(f"== 连刷 {i + 1}/{len(levels)}：第 {level} 关 ==")
    try:
        ok, msg = play_one(context, level)
    except Exception:
        msg = "异常：\n" + traceback.format_exc()
        log(msg)
        ok = False
    dt = time.time() - t0
    if ok:
        _STATE["ok"].append(level)
        log(f"   第 {level} 关 成功（{dt:.1f}s）：{msg}")
    else:
        _STATE["fail"].append(level)
        log(f"   第 {level} 关 失败（{dt:.1f}s）：{msg}")

    _STATE["i"] = i + 1
    if _STATE["i"] >= len(levels):
        spent = time.time() - _STATE["t0"]
        tail = ("；失败 " + ",".join(f"第{v}关" for v in _STATE["fail"])) if _STATE["fail"] else ""
        log(f"迷踪盘连刷 完成：成功 {len(_STATE['ok'])} 关{tail}，总用时 {spent / 60:.1f} 分钟")
        return False, "全部跑完，收尾退大厅"
    return True, f"下一关 第 {levels[_STATE['i']]} 关"


def register() -> None:
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction

    @AgentServer.custom_action("迷踪盘连刷")
    class FarmMizongpan(CustomAction):
        def run(self, context, argv) -> bool:
            param = {}
            try:
                param = json.loads(argv.custom_action_param or "{}")
            except Exception:
                log("连刷 param 解析失败：\n" + traceback.format_exc())
            try:
                cont, msg = run_farm(context, param)
            except Exception:
                msg = "迷踪盘连刷异常：\n" + traceback.format_exc()
                log(msg)
                cont = False
            log(f"迷踪盘连刷 节点返回 {'继续' if cont else '结束'}：{msg}")
            return CustomAction.RunResult(success=bool(cont))


def main(argv: list[str]) -> int:
    log("=" * 60)
    if "--levels" in argv:
        i = argv.index("--levels")
        a = int(argv[i + 1])
        b = int(argv[i + 2]) if len(argv) > i + 2 else a
        log(f"区间自检：{a}→{b} = {list(range(a, b + 1))}")
        return 0
    log("迷踪盘连刷：只作为 Custom action 使用；命令行支持 --levels A B 做区间自检")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
