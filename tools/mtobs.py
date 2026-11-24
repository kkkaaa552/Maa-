"""六边形阻挡语义实验：找一根「环上有多子且至少一子要撞进六边形」的轴，
点下去，比较两种模型。

模型 A（全阻）：环上任一子要进六边形 → 整次点击无效
模型 B（分阻）：只有撞六边形的那个子不动，其余照转

用法：python tools/mtobs.py
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


def tap(x, y, w=1.1):
    subprocess.run([ADB, "-s", ADDR, "shell", "input", "tap", str(x), str(y)], check=True)
    time.sleep(w)


def ring_detail(axis, P, H):
    """返回 [(dir_idx, cell, dest_idx, dest_cell, blocked?)]；dest 取 DIRS 序下一格。"""
    q, r = axis
    avail = [M.DIRS.index(d) for d, _ in M.neighbors(axis)]
    out = []
    for i in avail:
        cell = (q + M.DIRS[i][0], r + M.DIRS[i][1])
        if cell not in P:
            continue
        j = i
        for _ in range(6):
            j = (j + 1) % 6
            if j in avail:
                break
        dest = (q + M.DIRS[j][0], r + M.DIRS[j][1])
        out.append((i, cell, j, dest, dest in H))
    return out


def model(P, H, axis, mode):
    detail = ring_detail(axis, P, H)
    if not detail:
        return frozenset(P)
    if mode == "all" and any(d[4] for d in detail):
        return frozenset(P)
    new = set(P)
    for i, cell, j, dest, blk in detail:
        new.discard(cell)
        new.add(cell if (blk and mode == "each") else dest)
    return frozenset(new)


def main() -> int:
    b = M.read_board(cap())
    P, H, T = b["pieces"], b["hexes"], b["targets"]
    print(f"棋子 {sorted(P)}\n六边形 {sorted(H)}\n目标 {sorted(T)}")
    cand = None
    for ax in M.inner_axes():
        d = ring_detail(ax, P, H)
        if len(d) >= 2 and any(x[4] for x in d):
            cand = (ax, d)
            break
    if cand is None:
        print("没有「多子 + 撞六边形」的轴，本局无法区分两种模型")
        for ax in M.inner_axes():
            d = ring_detail(ax, P, H)
            if d and any(x[4] for x in d):
                print(f"  单子撞六边形可测: {ax} -> {d}")
        return 0
    ax, d = cand
    print(f"\n选中轴 {ax}，环上细节 {d}")
    pa = model(P, H, ax, "all")
    pb = model(P, H, ax, "each")
    print(f"模型A(全阻) 预测 {sorted(pa)}")
    print(f"模型B(分阻) 预测 {sorted(pb)}")
    tap(*M.CELLS[ax], 1.2)
    real = M.read_board(cap())["pieces"]
    print(f"实测        {sorted(real)}")
    print(f"  等于A? {sorted(real) == sorted(pa)}   等于B? {sorted(real) == sorted(pb)}"
          f"   等于原状? {sorted(real) == sorted(P)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
