"""对当前实机盘面跑三种语义求解，并用新 rotate 逐步推演，检查路径自洽。

用法：python tools/mtcheck.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import mtboard as M  # noqa: E402

ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
ADDR = "127.0.0.1:16384"


def cap():
    raw = subprocess.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
                         capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def short(s):
    return "{" + ",".join(f"{a},{b}" for a, b in sorted(s)) + "}"


def walk(P, T, H, path):
    cur = frozenset(P)
    for i, ax in enumerate(path):
        cur = M.rotate(cur, ax, blocked=H)
        print(f"   [{i}] {ax} -> {short(cur)}")
    return cur


def main() -> int:
    b = M.read_board(cap())
    P, H, T = b["pieces"], b["hexes"], b["targets"]
    print(f"棋子 {short(P)}\n六边形 {short(H)}\n目标 {short(T)}")
    print(f"棋子数 {len(P)} 目标数 {len(T)} 差 {len(P)-len(T)}\n")
    for name, axes in (("inner", M.inner_axes()), ("all", frozenset(M.CELLS))):
        path = M.solve(P, T, blocked=H, axes=axes)
        print(f"solve[{name}] -> {path}")
        if path:
            end = walk(P, T, H, path)
            print(f"   末端 {short(end)} {'== 目标 ✓' if end == T else '✗ 未达目标'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
