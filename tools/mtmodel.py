"""在失败盘面上枚举「旋转语义模型 × 可点轴集合」的组合，看哪一种模型有解。

模型：
  atomic  环上任一子要进六边形 → 整次无效（当前实现）
  pass    六边形不阻挡，子可落在六边形上
  stay    撞六边形的那个子原地不动，其余照转（可能撞车，撞车则整次无效）
轴集合：
  inner   只有 6 邻点齐全的 7 个格
  all     19 格全试（环缺格的方向跳过）

用法：python tools/mtmodel.py <盘面.png>
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


def load(path: str | None):
    if path and Path(path).exists():
        return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    raw = subprocess.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
                         capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def moves_of(axis, state, H, model):
    """返回 (new_state, ok)。ok=False 表示整次点击无效。"""
    q, r = axis
    avail = [M.DIRS.index(d) for d, _ in M.neighbors(axis)]
    pairs = []
    for i in avail:
        cell = (q + M.DIRS[i][0], r + M.DIRS[i][1])
        if cell not in state:
            continue
        j = i
        for _ in range(6):
            j = (j + 1) % 6
            if j in avail:
                break
        pairs.append((cell, (q + M.DIRS[j][0], r + M.DIRS[j][1])))
    if not pairs:
        return frozenset(state), True
    hits = [(c, d) for c, d in pairs if d in H]
    if hits:
        if model == "atomic":
            return frozenset(state), False
        if model == "pass":
            pass
        else:  # stay
            kept = {c for c, d in pairs if d in H}
            moved = [(c, d) for c, d in pairs if d not in H]
            new = set(state) - {c for c, _d in moved}
            for _c, d in moved:
                if d in new:
                    return frozenset(state), False
                new.add(d)
            return frozenset(new), True
    new = set(state) - {c for c, _d in pairs}
    for _c, d in pairs:
        if d in new:
            return frozenset(state), False
        new.add(d)
    return frozenset(new), True


def bfs(P, T, H, axes, model, limit=30):
    if P == T:
        return []
    seen = {P: None}
    q = deque([(P, 0)])
    while q:
        cur, dep = q.popleft()
        if dep >= limit:
            continue
        for ax in axes:
            nxt, ok = moves_of(ax, cur, H, model)
            if not ok or nxt == cur or nxt in seen:
                continue
            seen[nxt] = (cur, ax)
            if nxt == T:
                path, node = [ax], cur
                while seen[node] is not None:
                    node, a = seen[node]
                    path.append(a)
                return list(reversed(path))
            q.append((nxt, dep + 1))
    return None


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else None
    img = load(path)
    b = M.read_board(img)
    P, T, H = b["pieces"], b["targets"], b["hexes"]
    print(f"棋子 {sorted(P)}\n目标 {sorted(T)}\n六边形 {sorted(H)}  棋子数 {len(P)} 目标数 {len(T)}\n")
    for model in ("atomic", "pass", "stay"):
        for axname, axes in (("inner", M.inner_axes()), ("all", sorted(M.CELLS))):
            plan = bfs(P, T, frozenset(H), axes, model, limit=30)
            n = "无解" if plan is None else f"{len(plan)} 步 {plan}"
            print(f"{model:7s} {axname:6s} → {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
