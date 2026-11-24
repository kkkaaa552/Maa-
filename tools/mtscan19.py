"""19 格轴穷举扫描：每格都「悔棋 → 点 → 读数」，记录确切状态变化，
并打印三种旋转模型的预测，直接对照。

用法：python tools/mtscan19.py
输出：每格一行，给出实测新棋面 + 与各模型是否一致
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
UNDO = (500, 592)


def cap():
    raw = subprocess.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
                         capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def tap(p, w=1.0):
    subprocess.run([ADB, "-s", ADDR, "shell", "input", "tap", str(p[0]), str(p[1])],
                   check=True)
    time.sleep(w)


def pred_atomic(P, H, ax):
    return frozenset(M.rotate(P, ax, blocked=frozenset(H)))


def pred_pass(P, H, ax):
    return frozenset(M.rotate(P, ax, blocked=frozenset()))


def pred_stay(P, H, ax):
    """撞六边形的子原地不动，其余按序转；若目标被占则视为整次无效。"""
    q, r = ax
    avail = [M.DIRS.index(d) for d, _ in M.neighbors(ax)]
    pairs = []
    for i in avail:
        cell = (q + M.DIRS[i][0], r + M.DIRS[i][1])
        if cell not in P:
            continue
        j = i
        for _ in range(6):
            j = (j + 1) % 6
            if j in avail:
                break
        pairs.append((cell, (q + M.DIRS[j][0], r + M.DIRS[j][1])))
    if not pairs:
        return frozenset(P)
    moved = [(c, d) for c, d in pairs if d not in H]
    new = set(P) - {c for c, _d in moved}
    for _c, d in moved:
        if d in new:
            return frozenset(P)
        new.add(d)
    return frozenset(new)


def short(s):
    return "{" + ",".join(f"{a},{b}" for a, b in sorted(s)) + "}"


def main() -> int:
    tap(UNDO, 1.3)
    b = M.read_board(cap())
    P0, H, T = b["pieces"], b["hexes"], b["targets"]
    print(f"初始 棋子 {short(P0)}\n     六边形 {short(H)}\n     目标 {short(T)}\n")
    hits = {"atomic": 0, "pass": 0, "stay": 0}
    rows = []
    for ax in sorted(M.CELLS, key=lambda c: (c[1], c[0])):
        tap(UNDO, 1.15)
        base = M.read_board(cap())["pieces"]
        tap(M.CELLS[ax], 1.05)
        got = M.read_board(cap())["pieces"]
        pa, pp, ps = pred_atomic(base, H, ax), pred_pass(base, H, ax), pred_stay(base, H, ax)
        tags = []
        if got != base:
            if got == pa:
                tags.append("atomic")
            if got == pp:
                tags.append("pass")
            if got == ps:
                tags.append("stay")
        else:
            if pa == base:
                tags.append("atomic(空转)")
        for t in tags:
            k = t.split("(")[0]
            if k in hits:
                hits[k] += 1
        rows.append((ax, base == P0, got, tags))
        print(f"{str(ax):9s} 复位{'✓' if base == P0 else '✗'} 点后 {short(got):28s} "
              f"{'没动' if got == base else '变化'} {','.join(tags)}")
    print(f"\n模型命中计数（仅统计发生了变化的格）：{hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
