# -*- coding: utf-8 -*-
"""迷踪盘盘面读数 + 求解（1280x720 实机截图，MaaFramework 画布 1:1）。

读数依据（全部实测标定，见下）：
  棋子   : 格内 r<=11 的灰度 <160 像素占比 dkFrac > 0.5
           实测 棋子 0.69~1.00 / 褐色六边形 0.28 / 空格 0.00
  目标   : 以左侧小盘为准——绿色实心点，HSV inRange(35,25,40)-(95,255,180)
           connectedComponents 面积 600~1000、距格心 <8px
           （实测面积 807~816；大盘金环只在开局首帧出现，点一次即消失，
             故只能作交叉校验，不能作权威来源）
  六边形 : 必须先判——R-B > 45 且 G-R < -10（偏褐），静态不参与旋转、棋子不得落入
           实测六边形 R-B 57 / G-R -18，棋子 R-B<=18 / G-R>=-3，两侧分离

旋转语义（实测确认）：点轴 A，A 的 6 个 1 环邻点上索引 i 的棋子移到 i+1
（DIRS 屏幕顺时针序），轴心自身不动，六边形不动。

旋转方向是**盘面全局状态**：右下方 (1170,590) 的「顺/逆」按钮切换整个棋盘
的旋转方向，切完一直保持到再切一次。所以求解状态必须带方向维度——见
solve_bi()。实测某一局只按顺时针算时 7 个内圈轴全部无解，切逆时针 6 步即通。
"""
from __future__ import annotations

import sys
from collections import deque

import cv2
import numpy as np

# ---- 大盘 19 格六边形几何（1280x720 绝对坐标，Hough 实测）----
CX, CY = 838.0, 418.0
DX, DY = 130.0, 118.0

# ---- 左侧小盘 19 格几何（Hough 实测）----
SX, SY = 308.0, 288.0
SDX, SDY = 63.5, 57.0

# 六个轴向单位向量，按屏幕顺时针排序：右 → 右下 → 左下 → 左 → 左上 → 右上
DIRS = [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]

PIECE_DKFRAC = 0.5      # 棋子判据：灰度<160 像素占比
# 褐色六边形判据：必须先判 hex 再判 piece。
# 实测（debug/shots/now5.png 两块棋盘）：
#   六边形 R-B 57.1~57.5、G-R -17.6~-18.3、mean 145~160、dk(160) 0.488~1.000
#   棋子   R-B  4.6~17.6、G-R  -3.2~+12.1、dk(160) 0.87~1.00
#   空格   R-B <=30、     G-R  -5~0
# 只看灰度分不开：六边形半透明褐块在 dk<150 时跳到 0.9，与棋子同档；
# 但色彩上 R-B 与 G-R 两侧完全分离（R-B 45 处留 12 点余量，G-R -10 处留 7 点余量）。
HEX_RB = 45.0           # 六边形 R-B 下限
HEX_GR = -10.0          # 六边形 G-R 上限（偏褐 = 绿远低于红）


def _lattice(cx, cy, dx, dy) -> dict:
    out = {}
    for r in range(-2, 3):
        for q in range(-2, 3):
            if max(abs(q), abs(r), abs(q + r)) <= 2:
                out[(q, r)] = (cx + dx * (q + r / 2.0), cy + dy * r)
    return out


CELLS = _lattice(CX, CY, DX, DY)
SMALL = _lattice(SX, SY, SDX, SDY)


def neighbors(c) -> list:
    """c 的 6 个 1 环邻点在盘内的部分，按 DIRS 顺序返回 [(dir, cell)]。"""
    q, r = c
    return [(d, (q + d[0], r + d[1])) for d in DIRS if (q + d[0], r + d[1]) in CELLS]


def rotate(state: frozenset, axis, cw: bool = True, blocked=frozenset()):
    """绕 axis 把 1 环邻点上的棋子转 60°；axis 自身不动，盘外方向跳过。

    blocked 是六边形格。**旋转语义（tools/mtscan19.py 19 格穷举实测）**：
      1. 环上要转进六边形的那个棋子**原地不动**，成为本次旋转的障碍；
         其余棋子照常前进——不是「整次点击都无效」。
      2. 其余棋子是**同时置换**：目标格原本是否被另一个正在移动的棋子占着
         不算冲突（(0,0) 实测：(1,0)→(0,1) 同时 (0,1)→(-1,1) 成立）。
      3. 但若某子的目标格正是那个「原地不动」的棋子所在格 → 整次点击无效。
         这正是 mtobs.py 那次「点 (0,0) 三个子全不动」的真正原因：链式移动
         的最后一步撞上了被六边形卡住的 (-1,0)。
    """
    q, r = axis
    nb = neighbors(axis)
    avail = [DIRS.index(d) for d, _ in nb]
    ring = {}
    for d, cell in nb:
        if cell in state:
            ring[DIRS.index(d)] = cell
    if not ring:
        return state
    step = 1 if cw else -1
    pairs = []
    for i, src in ring.items():
        j = (i + step) % 6
        guard = 0
        while j not in avail and guard < 6:
            j = (j + step) % 6
            guard += 1
        pairs.append((src, (q + DIRS[j][0], r + DIRS[j][1])))
    stuck = {src for src, dest in pairs if dest in blocked}
    moving = [(src, dest) for src, dest in pairs if src not in stuck]
    dests = {dest for _src, dest in moving}
    if len(dests) != len(moving):
        return state
    if dests & stuck:
        return state
    new = set(state) - {src for src, _dest in moving}
    new |= dests
    return frozenset(new)


def inner_axes() -> list:
    """6 个邻点全部在盘内的轴（7 个），移动语义绝对安全。"""
    return sorted(c for c in CELLS if len(neighbors(c)) == 6)


def solve(pieces: frozenset, targets: frozenset, blocked=frozenset(), axes=None, limit=20):
    """BFS 最小点击序列，使棋子集合 == 目标集合。

    blocked（六边形格）既不能有子落入，也会让整次旋转失效——两个约束都由
    rotate() 的 blocked 参数表达，这里只需把它透传下去。早期版本在无解时用
    `solve(P,T,frozenset())` 把六边形当空地重算，那会给出在真机上根本按不动的
    解法（实机连续 13 次点同一轴毫无反应），这条兜底已删除。
    """
    axes = axes if axes is not None else inner_axes()
    pieces = frozenset(pieces)
    blocked = frozenset(blocked)
    if pieces == targets:
        return []
    seen = {pieces: None}
    q = deque([(pieces, 0)])
    while q:
        cur, depth = q.popleft()
        if depth >= limit:
            continue
        for ax in axes:
            nxt = rotate(cur, ax, blocked=blocked)
            if nxt == cur or nxt in seen:
                continue
            seen[nxt] = (cur, ax)
            if nxt == targets:
                path, node = [ax], cur
                while seen[node] is not None:
                    node, a = seen[node]
                    path.append(a)
                return list(reversed(path))
            q.append((nxt, depth + 1))
    return None


def solve_bi(pieces, targets, blocked=frozenset(), cw: bool = True, axes=None, limit=16):
    """双向 BFS：状态 = (棋子集合, 当前旋转方向)，动作 = 点某轴 / 切换方向。

    为什么必须带方向：盘面右下角有个「顺/逆」开关，它改的是**全局旋转方向**，
    不是单次点击的方向。实测这一局只按顺时针求解时 7 个内圈轴全部无解，
    切到逆时针 6 步就通——方向是棋盘状态的一部分，不能当常量。

    返回动作序列，元素为 ('tap', axis) 或 ('flip', None)；无解返回 None。
    """
    axes = axes if axes is not None else inner_axes()
    pieces = frozenset(pieces)
    blocked = frozenset(blocked)
    targets = frozenset(targets)
    if pieces == targets:
        return []
    start = (pieces, bool(cw))
    seen = {start: None}
    q = deque([(start, 0)])
    while q:
        (P, d), depth = q.popleft()
        if depth >= limit:
            continue
        for ax in axes:
            nxt = rotate(P, ax, cw=d, blocked=blocked)
            if nxt != P:
                st = (nxt, d)
                if st not in seen:
                    seen[st] = ((P, d), ("tap", ax))
                    if nxt == targets:
                        acts = [("tap", ax)]
                        node = (P, d)
                        while seen[node] is not None:
                            node, act = seen[node]
                            acts.append(act)
                        return list(reversed(acts))
                    q.append((st, depth + 1))
        st = (P, not d)
        if st not in seen:
            seen[st] = ((P, d), ("flip", None))
            q.append((st, depth + 1))
    return None


# ---------------- 读数 ----------------

def _inner(img_f, gray, x, y):
    xi, yi = int(x), int(y)
    S = 20
    sub = img_f[yi - S:yi + S, xi - S:xi + S]
    gs = gray[yi - S:yi + S, xi - S:xi + S]
    yy, xx = np.mgrid[0:2 * S, 0:2 * S]
    d = np.sqrt((xx - S) ** 2 + (yy - S) ** 2)
    m = d <= 11
    b, g, r = sub[..., 0][m], sub[..., 1][m], sub[..., 2][m]
    return (float(gs[m].mean()), float((gs[m] < 160).mean()),
            float(r.mean() - b.mean()), float(g.mean() - r.mean()))


def read_board(img) -> dict:
    """返回 {'pieces':frozenset,'hexes':frozenset,'targets':frozenset,'stats':{}}"""
    img_f = img.astype(np.float32)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    pieces, hexes, stats = set(), set(), {}
    for c, (x, y) in CELLS.items():
        mean, dk, rb, gr = _inner(img_f, gray, x, y)
        stats[c] = (mean, dk, rb, gr)
        # 色彩先定 hex：褐色块在灰度上与棋子重叠，但在 R-B / G-R 上完全分离
        if rb > HEX_RB and gr < HEX_GR:
            hexes.add(c)
        elif dk > PIECE_DKFRAC:
            pieces.add(c)
    return {"pieces": frozenset(pieces), "hexes": frozenset(hexes),
            "targets": read_targets_small(img), "stats": stats}


def read_targets_small(img) -> frozenset:
    """左侧小盘绿色实心点 = 解密点（目标格）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (35, 25, 40), (95, 255, 180))
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
    out = set()
    for i in range(1, n):
        if not (550 <= st[i, cv2.CC_STAT_AREA] <= 1050):
            continue
        cx, cy = cen[i]
        best, bd = None, 1e9
        for c, (x, y) in SMALL.items():
            dd = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if dd < bd:
                bd, best = dd, c
        if bd < 9:
            out.add(best)
    return frozenset(out)


def read_targets_big(img) -> frozenset:
    """大盘金环（仅开局首帧可见，作交叉校验）：环带 r∈[30,38] 的 (R-B)>45 占比。"""
    im = img.astype(np.float32)
    out = set()
    for c, (x, y) in CELLS.items():
        xi, yi = int(x), int(y)
        S = 45
        sub = im[yi - S:yi + S, xi - S:xi + S]
        yy, xx = np.mgrid[0:2 * S, 0:2 * S]
        d = np.sqrt((xx - S) ** 2 + (yy - S) ** 2)
        m = (d >= 30) & (d <= 38)
        b, r = sub[..., 0], sub[..., 2]
        if float(((r - b)[m] > 45).mean()) > 0.6:
            out.add(c)
    return frozenset(out)


def read_pieces(img) -> frozenset:
    return read_board(img)["pieces"]


def label(state) -> str:
    rows = {}
    for c in CELLS:
        rows.setdefault(c[1], []).append(c)
    out = []
    for r in sorted(rows):
        out.append(f" r={r:+d}  " + "  ".join(
            f"{'#' if c in state else '.'}{c}" for c in sorted(rows[r])))
    return "\n".join(out)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"debug/shots/cur.png"
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"读不到 {path}")
    b = read_board(img)
    P, T, H = b["pieces"], b["targets"], b["hexes"]
    print(f"== {path}")
    print(label(P | T | H))
    print(f" 棋子: {sorted(P, key=lambda c: (c[1], c[0]))}")
    print(f" 目标: {sorted(T, key=lambda c: (c[1], c[0]))}")
    print(f" 六边形: {sorted(H, key=lambda c: (c[1], c[0]))}")
    big = read_targets_big(img)
    if big:
        print(f" 大盘金环(交叉校验): {sorted(big, key=lambda c: (c[1], c[0]))}"
              f"  与小盘一致={big == T}")
    print(f" 已达成: {P == T}")
    if P != T:
        for name, axes in (("内圈7轴", inner_axes()), ("全19轴", None)):
            p = solve(P, T, H, axes)
            print(f" 解法[{name}]: {'无解' if p is None else f'{len(p)} 步 {p}'}")


if __name__ == "__main__":
    main()
