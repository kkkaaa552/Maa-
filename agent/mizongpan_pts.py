# -*- coding: utf-8 -*-
"""迷踪盘点位 Custom action：照攻略点位表盲点，不读盘、不求解。

被 pipeline 节点 `迷踪盘.点位通关` 调用，动作名 `迷踪盘点位`。

为什么有这条路线（用户 2026-11-25 三次要求）：
    主线关卡 1-30 的盘面与目标点是固定的，攻略视频/截图上的荧光标注就是
    官方解：`12逆` = 第 1、2 步都在逆时针状态下点同一格，`3顺` = 第 3 步在
    顺时针状态下点该格。既然有现成答案，就不该再靠读盘 + BFS——读盘是这条
    链路上唯一的故障源（小盘绿点漏读、480P 帧逐帧抖动、弹窗压暗导致把
    网格线误判成棋子，都实测踩过）。盲点不会错，因为它不依赖任何读数。

点位表来源：`agent/mizongpan_points.json`
    {cell_px: {"q,r": [px, py]}, levels: {"1": {steps, moves: [[q,r,方向,px,py]]}}}
    moves 里的 px/py 就是 1280x720 画布上可直接点击的坐标，不需要再换算。

执行语义（逐步）：
    对每一步 [q, r, dir, px, py]：
      1. 若当前旋转方向 != dir → 点右下 (1170,590) 切一次，等 DIR_WAIT；
      2. 点 (px, py)，等 TAP_WAIT。
    只有方向不同才切，所以「1逆 2顺」这种序列不会来回空切。

收尾判定三级（不认「没读到棋子」为成功，旧版求解器就在这里误报过）：
    1. 命中「已达到移动次数上限」弹窗 → 说明点位步数超过了本关上限，
       点蓝勾复位后整串重来（最多 MAX_RESTARTS 次），再不行返回失败；
    2. 命中通关遮罩「完成」模板 → 成功；
    3. 读盘比对 棋子 == 目标 → 成功；否则返回失败。

失败返回 False 后，pipeline 的 on_error 会把控制权交给 `迷踪盘.求解`
（求解器兜底），所以即使点位表某一关不准，整条任务也不会卡死。

离线自检（不点）：
    .venv\\Scripts\\python.exe agent\\mizongpan_pts.py --plan 4
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

POINTS_PATH = ROOT / "agent" / "mizongpan_points.json"

TAP_WAIT = 0.75          # 单次点击后等动画（与 mizongpan.py 同口径）
DIR_WAIT = 1.8           # 切方向后的等待：切换带过渡动画，紧跟的点击会被吞
BOARD_WAIT = 10.0        # 等棋盘出现（右下顺/逆按钮可读）的最长秒数
POLL = 0.35
DIR_BTN = (1170, 590)    # 右下「顺/逆」菱形按钮
BTN_V = (771, 428)       # 超限弹窗蓝勾中心（实测 OCR V 框 757,419,25,20）
MAX_RESTARTS = 2         # 超限弹窗复位重来的次数上限
FINISH_WAIT = 6.0        # 通关遮罩入场动画最长等待（实测 0.75s 太早、3s 命中）

FINISH_ROI = (650, 320, 420, 220)   # 通关遮罩「完成」书法字
FINISH_TPL = "迷踪盘.完成.png"


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_points(level: int) -> list:
    """读点位表里第 level 关的 moves 列表。"""
    d = json.loads(POINTS_PATH.read_text(encoding="utf-8"))
    levels = d.get("levels", {})
    lv = levels.get(str(level))
    if lv is None:
        raise KeyError(f"点位表里没有第 {level} 关（现有 {sorted(levels, key=int)}）")
    return [list(m) for m in lv.get("moves", [])]


def _load_tpl(name: str):
    import cv2
    import numpy as np
    p = ROOT / "resource" / "image" / name
    if not p.exists():
        return None
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def run_points(ctl, level: int, moves=None, dry: bool = False) -> tuple:
    """按点位表点完一关。返回 (ok, msg)。ctl 需有 post_screencap / post_click。"""
    import cv2
    from mtboard import (read_board, read_dialog_confirm, read_dir)

    if moves is None:
        moves = load_points(level)
    if not moves:
        return False, f"第 {level} 关点位为空（攻略未标注该关）"

    if dry:
        for i, (q, r, d, px, py) in enumerate(moves, 1):
            log(f"  第 {i} 步 格({q},{r}) {d} → ({px},{py})")
        return True, f"dry-run：第 {level} 关共 {len(moves)} 步"

    def grab():
        img = ctl.post_screencap().wait().get()
        if img is None:
            raise RuntimeError("截图返回空")
        return img

    def tap_at(x, y):
        ctl.post_click(int(x), int(y)).wait()

    tpl = _load_tpl(FINISH_TPL)

    def finish_hit(img) -> tuple:
        if tpl is None:
            return False, 0.0
        x, y, w, h = FINISH_ROI
        sub = img[y:y + h, x:x + w]
        if sub.shape[0] < tpl.shape[0] or sub.shape[1] < tpl.shape[1]:
            return False, 0.0
        s = float(cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED).max())
        return (s >= 0.70), s

    # 1) 等棋盘：右下顺/逆按钮读得出来就说明已经在棋盘页
    t0 = time.time()
    cur = None
    while time.time() - t0 < BOARD_WAIT:
        try:
            img = grab()
        except Exception:
            img = None
        if img is not None:
            d, score = read_dir(img)
            if d is not None:
                cur = d
                log(f"棋盘就绪：当前方向「{d}」(NCC {score:.3f})")
                break
        time.sleep(POLL)
    if cur is None:
        return False, "等不到棋盘（右下顺/逆按钮读不出，可能不在棋盘页）"

    log(f"开始点位：第 {level} 关，共 {len(moves)} 步")

    restarts = 0
    while True:
        aborted = None
        for i, (q, r, want, px, py) in enumerate(moves, 1):
            if want != cur:
                tap_at(*DIR_BTN)
                time.sleep(DIR_WAIT)
                img = grab()
                got, score = read_dir(img)
                if got is None:
                    log(f"[{i}] 切方向后读不出按钮（NCC {score:.3f}），按预期「{want}」继续")
                    cur = want
                else:
                    cur = got
                    if got != want:
                        log(f"[{i}] 期望「{want}」但按钮读成「{got}」，再切一次")
                        tap_at(*DIR_BTN)
                        time.sleep(DIR_WAIT)
                        got2, s2 = read_dir(grab())
                        cur = got2 or want
                        log(f"[{i}] 二次切换后读成「{cur}」(NCC {s2:.3f})")
                log(f"[{i}] 切方向 → {cur}")

            tap_at(px, py)
            time.sleep(TAP_WAIT)

            # 每一步都看一眼有没有超限弹窗：它会把棋盘压暗，后续点击全部无效
            try:
                img = grab()
            except Exception:
                img = None
            if img is not None:
                hit, dscore = read_dialog_confirm(img)
                if hit:
                    restarts += 1
                    if restarts > MAX_RESTARTS:
                        return False, (f"第 {i}/{len(moves)} 步触发移动次数上限，"
                                       f"已复位 {MAX_RESTARTS} 次仍未通关")
                    log(f"[{i}] 触发「移动次数上限」弹窗（NCC {dscore:.3f}）"
                        f"→ 点蓝勾复位，整串重来（第 {restarts} 次）")
                    tap_at(*BTN_V)
                    time.sleep(1.8)
                    img = grab()
                    d, _s = read_dir(img)
                    cur = d
                    log(f"    复位后方向「{cur}」")
                    aborted = "restart"
                    break
        if aborted != "restart":
            break

    # 2) 收尾判定：通关遮罩有入场动画，最后一步点完 0.75s 时还没出来
    #    （实测第 2 关：点完立刻查 NCC 0.139，等 3s 后 0.7774 命中），所以轮询。
    t0 = time.time()
    ok, s = False, 0.0
    img = None
    while time.time() - t0 < FINISH_WAIT:
        try:
            img = grab()
        except Exception:
            img = None
        if img is not None:
            ok, s = finish_hit(img)
            if ok:
                return True, (f"第 {level} 关点位 {len(moves)} 步完成，"
                              f"通关遮罩已出现（NCC {s:.3f}，等待 {time.time() - t0:.1f}s）")
        time.sleep(POLL)
    if img is not None:
        b = read_board(img)
        if b["targets"] and sorted(b["pieces"]) == sorted(b["targets"]):
            return True, f"第 {level} 关点位 {len(moves)} 步完成，棋子已全部归位"
        return False, (f"第 {level} 关点位 {len(moves)} 步点完但未判定通关："
                       f"完成模板 NCC {s:.3f}（等了 {FINISH_WAIT:.0f}s）、"
                       f"棋子 {sorted(b['pieces'], key=lambda c: (c[1], c[0]))} "
                       f"目标 {sorted(b['targets'], key=lambda c: (c[1], c[0]))}")
    return False, f"第 {level} 关点位点完但收尾截图一直失败"


def register() -> None:
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction

    @AgentServer.custom_action("迷踪盘点位")
    class PointMizongpan(CustomAction):
        def run(self, context, argv) -> bool:
            level = 0
            try:
                param = json.loads(argv.custom_action_param or "{}")
                level = int(param.get("level") or 0)
            except Exception:
                log("custom_action_param 解析失败：\n" + traceback.format_exc())
            if level <= 0:
                log("level=0（体验关卡或未指定）→ 不点位，交回 pipeline 走求解器")
                return CustomAction.RunResult(success=False)
            try:
                ok, msg = run_points(context.tasker.controller, level)
            except Exception:
                msg = "迷踪盘点位异常：\n" + traceback.format_exc()
                log(msg)
                ok = False
            log(f"迷踪盘点位 {'成功' if ok else '失败'}：{msg}")
            return CustomAction.RunResult(success=bool(ok))


def main(argv: list[str]) -> int:
    log("=" * 60)
    if "--plan" in argv:
        i = argv.index("--plan")
        level = int(argv[i + 1]) if len(argv) > i + 1 else 1
        ok, msg = run_points(None, level, dry=True)
        log(msg)
        return 0 if ok else 1
    p = POINTS_PATH
    if not p.exists():
        log(f"点位表不存在：{p}")
        return 1
    d = json.loads(p.read_text(encoding="utf-8"))
    levels = d.get("levels", {})
    log(f"点位表 {p.name}：{len(levels)} 关")
    for k in sorted(levels, key=int):
        lv = levels[k]
        log(f"  第 {k:>2} 关  {lv['steps']:>2} 步  {lv.get('file', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
