# -*- coding: utf-8 -*-
"""迷踪盘闭环求解器（助手侧取证 / 也是 Custom action 的原型）。

每一步都重新截图读数，与预测比对；不一致就按实际状态重规划，
连续 3 次同一首步失败则放开「六边形不可落子」的保守假设。

用法：
    python tools/mtrun.py               # 从当前画面开始解，最多 24 步
    python tools/mtrun.py --dry         # 只读盘与算解法，不点
    python tools/mtrun.py --save d1     # 每步存 debug/shots/d1_0.png ...
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mtboard import CELLS, inner_axes, read_board, rotate, solve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
ADDR = "127.0.0.1:16384"
TAP_DELAY = 0.75


def adb(*args: str) -> str:
    r = subprocess.run([ADB, "-s", ADDR, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=25)
    return (r.stdout or "") + (r.stderr or "")


def screencap() -> np.ndarray:
    raw = subprocess.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
                         capture_output=True, timeout=30).stdout
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("截图解码失败")
    return img


def tap(c) -> None:
    x, y = CELLS[c]
    adb("shell", "input", "tap", str(int(x)), str(int(y)))


def sortc(s):
    return sorted(s, key=lambda c: (c[1], c[0]))


def run(dry=False, save=None, max_steps=24, log=print) -> str:
    img = screencap()
    b = read_board(img)
    P, T, H = b["pieces"], b["targets"], b["hexes"]
    log(f"棋子 {sortc(P)}")
    log(f"目标 {sortc(T)}")
    log(f"六边形 {sortc(H)}")
    if not T:
        return "no-targets"
    if (P & T) == P and P == T:
        log("开局即达成")
        return "solved"
    blocked = set(H)
    stuck = {}
    for step in range(max_steps):
        plan = solve(P, T, frozenset(blocked)) or solve(P, T, frozenset())
        if plan is None:
            log(f"第 {step} 步无解（blocked={sortc(blocked)}）")
            return "unsolvable"
        ax = plan[0]
        pred = rotate(P, ax)
        log(f"[{step}] 计划 {len(plan)} 步 首步 {ax} -> 预测 {sortc(pred)}")
        if dry:
            log(f"     完整计划 {plan}")
            return "dry"
        tap(ax)
        time.sleep(TAP_DELAY)
        img = screencap()
        if save:
            cv2.imwrite(str(ROOT / "debug" / "shots" / f"{save}_{step}.png"), img)
        b = read_board(img)
        P2, T2 = b["pieces"], b["targets"]
        if T2 and T2 != T:
            log(f"     目标集变化 {sortc(T)} -> {sortc(T2)}")
            T = T2
        if P2 != pred:
            log(f"     实测 {sortc(P2)} 与预测不符")
            stuck[ax] = stuck.get(ax, 0) + 1
            if stuck[ax] >= 3:
                blocked.discard(ax)
        P = P2
        if sorted(P) == sorted(T):
            log(f"[{step}] 已达成 棋子 {sortc(P)}")
            return "solved"
    return "max-steps"


def main(argv) -> int:
    dry = "--dry" in argv
    save = None
    if "--save" in argv:
        save = argv[argv.index("--save") + 1]
    print(run(dry=dry, save=save))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
