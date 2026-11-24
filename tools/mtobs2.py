"""六边形与旋转的三种情形定量实验。

情形 1：六边形在轴心 → 轴心不动（已知，装饰物）
情形 2：六边形在环上、无子要进它 → 应照转
情形 3：六边形在环上、有子要进它 → 待定（全阻 / 分阻 / 子自旋占位）

用法：python tools/mtobs2.py
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

ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
ADDR = "127.0.0.1:16384"


def cap():
    raw = subprocess.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
                         capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def tap(x, y, w=1.2):
    subprocess.run([ADB, "-s", ADDR, "shell", "input", "tap", str(x), str(y)], check=True)
    time.sleep(w)


def ring(axis, P):
    q, r = axis
    out = []
    for i in (M.DIRS.index(d) for d, _ in M.neighbors(axis)):
        cell = (q + M.DIRS[i][0], r + M.DIRS[i][1])
        out.append((i, cell, cell in P))
    return out


def main() -> int:
    b = M.read_board(cap())
    P, H = b["pieces"], b["hexes"]
    print(f"棋子 {sorted(P)}\n六边形 {sorted(H)}\n")
    for ax in M.inner_axes():
        rg = ring(ax, P)
        occ = [c for _i, c, has in rg if has]
        if not occ:
            continue
        # 该轴环上是否有子会转进六边形
        hit = []
        for idx, cell, has in rg:
            if not has:
                continue
            j = (idx + 1) % 6
            avail = [M.DIRS.index(d) for d, _ in M.neighbors(ax)]
            g = 0
            while j not in avail and g < 6:
                j = (j + 1) % 6
                g += 1
            dest = (ax[0] + M.DIRS[j][0], ax[1] + M.DIRS[j][1])
            if dest in H:
                hit.append((cell, dest))
        if hit:
            print(f"轴 {ax} 环上子 {occ}  撞六边形 {hit}")
    print("\n（只列会撞六边形的轴）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
