"""实机执行双向解：按动作序列点击，支持中间切方向，每步实测校验。

用法：
    python tools/mtrun2.py            # 实时读数求解并执行
    python tools/mtrun2.py --dry      # 只求解不点
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import mtboard as M  # noqa: E402
from mtbi import solve_bi  # noqa: E402

ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
ADDR = "127.0.0.1:16384"
DIRBTN = (1170, 590)
TAP_WAIT = 0.85


def cap():
    raw = subprocess.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
                         capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def tap(p, w=TAP_WAIT):
    subprocess.run([ADB, "-s", ADDR, "shell", "input", "tap", str(p[0]), str(p[1])],
                   check=True)
    time.sleep(w)


def short(s):
    return "{" + ",".join(f"{a},{b}" for a, b in sorted(s)) + "}"


def read_retry(n=3):
    for i in range(n):
        img = cap()
        b = M.read_board(img)
        if b["pieces"]:
            return b
        time.sleep(0.4)
    return b


def main(argv) -> int:
    dry = "--dry" in argv
    b = read_retry()
    P, H, T = b["pieces"], b["hexes"], b["targets"]
    print(f"开局 棋子 {short(P)} 目标 {short(T)} 六边形 {short(H)}")

    # 当前方向未知：分别假设顺/逆各求一次解，取步数更短的
    cands = []
    for cw0 in (True, False):
        acts = solve_bi(P, T, H, start_cw=cw0)
        if acts:
            cands.append((len(acts), cw0, acts))
    if not cands:
        print("双向仍无解——先切一次方向再试")
        if dry:
            return 1
        tap(DIRBTN, 1.0)
        for cw0 in (True, False):
            acts = solve_bi(P, T, H, start_cw=cw0)
            if acts:
                cands.append((len(acts), cw0, acts))
        if not cands:
            print("仍无解，放弃本局")
            return 1
    cands.sort()
    n, cw0, acts = cands[0]
    print(f"采用 起手{'顺' if cw0 else '逆'} 共 {n} 步: {acts}")
    if dry:
        return 0

    cur, cw = frozenset(P), cw0
    for i, (kind, ax) in enumerate(acts):
        if kind == "flip":
            tap(DIRBTN, 1.0)
            cw = not cw
            print(f"  [{i}] 切方向 -> {'顺' if cw else '逆'}")
            continue
        pred = M.rotate(cur, ax, cw=cw, blocked=H)
        tap(M.CELLS[ax])
        got = read_retry()["pieces"]
        ok = "✓" if got == pred else "✗"
        print(f"  [{i}] 点 {ax} ({'顺' if cw else '逆'}) 预测 {short(pred)} "
              f"实测 {short(got)} {ok}")
        if got != pred:
            print("  预测不符，中止（需人工核对语义）")
            return 2
        cur = pred
        if cur == T:
            print(f"  第 {i+1} 步达成目标")
            break
    print("solved" if cur == T else "未达成")
    return 0 if cur == T else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
