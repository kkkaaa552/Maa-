"""双向求解：状态 = (棋子集合, 旋转方向)，动作 = 各内圈轴点击 或 切换方向。

用法：python tools/mtbi.py [图片路径]
不带参数则实时截图读数。
"""
from __future__ import annotations

import subprocess
import sys
from collections import deque
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


def load(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def short(s):
    return "{" + ",".join(f"{a},{b}" for a, b in sorted(s)) + "}"


def solve_bi(pieces, targets, blocked, start_cw=True, axes=None, limit=16):
    """返回动作序列：('tap', axis) 或 ('flip',)。cw=True 表示顺时针。"""
    axes = axes if axes is not None else M.inner_axes()
    pieces = frozenset(pieces)
    blocked = frozenset(blocked)
    start = (pieces, bool(start_cw))
    if pieces == targets:
        return []
    seen = {start: None}
    q = deque([(start, 0)])
    while q:
        (P, cw), depth = q.popleft()
        if depth >= limit:
            continue
        succ = []
        for ax in axes:
            nxt = M.rotate(P, ax, cw=cw, blocked=blocked)
            if nxt != P:
                succ.append(("tap", ax, (nxt, cw)))
        succ.append(("flip", None, (P, not cw)))
        for kind, ax, st in succ:
            if st in seen:
                continue
            seen[st] = ((P, cw), (kind, ax))
            if st[0] == targets:
                acts, node = [(kind, ax)], (P, cw)
                while seen[node] is not None:
                    node, act = seen[node]
                    acts.append(act)
                return list(reversed(acts))
            q.append((st, depth + 1))
    return None


def main(argv) -> int:
    img = load(argv[1]) if len(argv) > 1 else cap()
    b = M.read_board(img)
    P, H, T = b["pieces"], b["hexes"], b["targets"]
    print(f"棋子 {short(P)}\n六边形 {short(H)}\n目标 {short(T)}  "
          f"(棋子 {len(P)} / 目标 {len(T)})")
    for start_cw in (True, False):
        acts = solve_bi(P, T, H, start_cw=start_cw)
        tag = "顺" if start_cw else "逆"
        print(f"\n起手{tag} -> {acts}")
        if acts:
            cur, cw = frozenset(P), start_cw
            for i, (kind, ax) in enumerate(acts):
                if kind == "flip":
                    cw = not cw
                    print(f"   [{i}] 切方向 -> {'顺' if cw else '逆'}")
                else:
                    cur = M.rotate(cur, ax, cw=cw, blocked=H)
                    print(f"   [{i}] 点 {ax} ({'顺' if cw else '逆'}) -> {short(cur)}")
            print(f"   末端 {'== 目标 ✓' if cur == T else '✗'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
