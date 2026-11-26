# -*- coding: utf-8 -*-
"""迷踪盘盘面读数 + 求解（1280x720 实机截图，MaaFramework 画布 1:1）。

读数依据（2026-11-25 重标定，见 tools/_mt_feat.py 与 tools/_mt_small_feat.py
的逐格特征矩阵）：

  逐格特征只用**通道顺序无关**的量：饱和度 (max-min)/max、亮度 max、
  以及「绿色超出量」G - max(B,R)。旧判据用带符号的通道差（R-B、G-R），
  在不同来源的截图上会翻车——实测 now5/dirbtn/failstate 三张是 RGB 顺序
  读进来的（px(470,420)=[106,139,147]），lv1_enter2/mt_restart1 是 BGR
  （[147,139,106]），同一块盘面在旧判据下读数完全不同。

  大盘 19 格（核心半径 7px，实测值域）：
    空格      : Sm 16~35、S90 23~36、Vm 216~240
    黑色棋子  : Sm 20~24、S90 23~31、Vm 57~67        （未到位的棋子）
    绿色棋子  : Sm 47~57、S90 51~60、Vm 118~136、gm +7~+13
                （**已停在自己的解密点上**的棋子，与左侧小盘绿点同色）
    褐色六边形（体验关卡）: Sm 84~91、S90 90~97、Vm 137~177、gm -19~-18
    蓝色六边形（主线关卡）: Sm 89、   S90 96、   Vm 161、   gm -17
  → 判别顺序必须是 hex → 棋子，因为六边形的饱和度和棋子重叠。
    六边形判 S90 >= 68（绿色棋子最高 60，留 8 点余量）；两种配色都吃。

  左侧小盘解密点：绿点 Sm 55~57、Vm 117~121、gm +12~+14；
    空格与小盘背景暗块 Sm 24~35、gm 恒负。
    → 判据 Sm >= 42 且 gm >= 5 且 Vm < 165，两个维度都留 20 点以上余量。

  为什么不再用连通域找绿点：旧 read_targets_small() 用 inRange +
  connectedComponents + 面积 550~1050，在主线关卡 lv1_enter2 上漏读了
  (2,-2) 的绿点——它和背景暗块粘成一个 3156 像素的连通域，被面积上限滤掉。
  结果求解器以为只有 1 个解密点，把「1 步就能通」的一局判成双向无解。
  逐格采样不受粘连影响。

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

# ---- 判据阈值（全部来自逐格实测，见模块 docstring 的值域表）----
HEX_S90 = 68.0          # 六边形：饱和度 P90 下限（绿棋子最高 60）
PIECE_DARK_DK = 0.5     # 棋子（黑/灰/绿）：格心灰度<160 占比下限
PIECE_GRN_SM = 40.0     # 绿色棋子：饱和度均值下限
PIECE_GRN_VM = 200.0    # 绿色棋子：亮度均值上限（空格最低 216）
DOT_SM = 42.0           # 小盘解密点：饱和度均值下限（小盘空格最高 36）
DOT_GM = 5.0            # 小盘解密点：绿色超出量下限（小盘空格恒负）
DOT_VM = 165.0          # 小盘解密点：亮度均值上限（小盘空格最低 166）

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

    blocked 是六边形格。**旋转语义（19 格穷举实测）**：
      1. 环上要转进六边形的那个棋子**原地不动**，成为本次旋转的障碍；
         其余棋子照常前进——不是「整次点击都无效」。
      2. 其余棋子是**同时置换**：目标格原本是否被另一个正在移动的棋子占着
         不算冲突（(0,0) 实测：(1,0)→(0,1) 同时 (0,1)→(-1,1) 成立）。
      3. 但若某子的目标格正是那个「原地不动」的棋子所在格 → 整次点击无效。
         这正是「点 (0,0) 三个子全不动」的真正原因：链式移动
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

def _cell_feat(img, x, y, core=7, S=9):
    """格心核心圆内的通道无关特征。

    返回 (饱和度均值, 饱和度P90, 亮度均值, 绿色超出量均值, 暗像素占比)。

    只用 max / (max-min)/max / G-max(B,R) / 灰度<160 占比，对 BGR 与 RGB
    两种通道顺序都成立，所以不受截图来源差异影响（旧判据的 R-B、G-R 在
    两种顺序下结论相反）。
    """
    xi, yi = int(round(x)), int(round(y))
    sub = img[yi - S:yi + S, xi - S:xi + S].astype(np.int32)
    yy, xx = np.mgrid[0:2 * S, 0:2 * S]
    d = np.sqrt((xx - S) ** 2 + (yy - S) ** 2)
    m = d <= core
    c0, c1, c2 = sub[..., 0][m], sub[..., 1][m], sub[..., 2][m]
    mx = np.maximum(np.maximum(c0, c1), c2).astype(np.float64)
    mn = np.minimum(np.minimum(c0, c1), c2).astype(np.float64)
    sat = np.where(mx > 0, 255.0 * (mx - mn) / np.maximum(mx, 1), 0.0)
    gexc = c1 - np.maximum(c0, c2)
    gray = 0.114 * c0 + 0.587 * c1 + 0.299 * c2
    return (float(sat.mean()), float(np.percentile(sat, 90)),
            float(mx.mean()), float(gexc.mean()),
            float((gray < 160).mean()))


def read_board(img) -> dict:
    """返回 {'pieces':frozenset,'hexes':frozenset,'targets':frozenset,'stats':{}}"""
    pieces, hexes, stats = set(), set(), {}
    for c, (x, y) in CELLS.items():
        sm, s90, vm, gm, dk = _cell_feat(img, x, y)
        stats[c] = (sm, s90, vm, gm, dk)
        if s90 >= HEX_S90:                       # 六边形优先：饱和度与棋子重叠
            hexes.add(c)
        elif sm >= PIECE_GRN_SM and vm < PIECE_GRN_VM and gm > DOT_GM:
            pieces.add(c)                        # 绿色棋子（已停在自己的解密点）
        elif dk >= PIECE_DARK_DK:
            pieces.add(c)                        # 黑/灰棋子（未到位）
    return {"pieces": frozenset(pieces), "hexes": frozenset(hexes),
            "targets": read_targets_small(img), "stats": stats}


def read_targets_small(img) -> frozenset:
    """左侧小盘绿色实心点 = 解密点（目标格）。

    逐格采样而不是连通域：主线关卡上 (2,-2) 的绿点会和背景暗块粘连成
    3000+ 像素的大连通域，面积过滤会把它丢掉（实测漏读，导致「1 步能通」
    的一局被判成无解）。
    """
    out = set()
    for c, (x, y) in SMALL.items():
        sm, _s90, vm, gm, _dk = _cell_feat(img, x, y)
        if sm >= DOT_SM and gm >= DOT_GM and vm < DOT_VM:
            out.add(c)
    return frozenset(out)


def read_targets_big(img) -> frozenset:
    """大盘金环（仅开局首帧可见，作交叉校验）：环带 r∈[30,38] 的高饱和占比。"""
    im = img.astype(np.float32)
    out = set()
    for c, (x, y) in CELLS.items():
        xi, yi = int(x), int(y)
        S = 45
        sub = im[yi - S:yi + S, xi - S:xi + S]
        yy, xx = np.mgrid[0:2 * S, 0:2 * S]
        d = np.sqrt((xx - S) ** 2 + (yy - S) ** 2)
        m = (d >= 30) & (d <= 38)
        b, g, r = sub[..., 0][m], sub[..., 1][m], sub[..., 2][m]
        mx = np.maximum(np.maximum(b, g), r)
        mn = np.minimum(np.minimum(b, g), r)
        sat = np.where(mx > 0, 255.0 * (mx - mn) / np.maximum(mx, 1), 0.0)
        if float((sat > 60).mean()) > 0.6:
            out.add(c)
    return frozenset(out)


DIALOG_ROI = (660, 380, 220, 100)   # 超限弹窗里蓝色「V」按钮所在带
DIALOG_TPL = "迷踪盘.弹窗确认.png"

_DLG_CACHE: dict = {}


def _load_dialog_template():
    if _DLG_CACHE:
        return _DLG_CACHE
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "resource" / "image" / DIALOG_TPL
    if p.exists():
        t = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
        if t is not None:
            _DLG_CACHE["t"] = t
    return _DLG_CACHE


def read_dialog_confirm(img) -> tuple:
    """是否停在「已达到移动次数上限，是否重新开始?」弹窗上：('V'|None, 分数)。

    为什么要有这个判据：主线关卡有移动次数上限，用完就弹窗，此时棋盘被压暗、
    再点任何格都没反应。求解器如果只按「盘面没动」判失败，会白耗两轮重试
    才退出来；更糟的是弹窗盖着棋盘，读数会当成「棋子全丢」。实测模板分离
    干净：弹窗页 1.0000，其余九张（棋盘/关卡页/大厅/通关遮罩）≤0.1990。
    """
    x, y, w, h = DIALOG_ROI
    sub = img[y:y + h, x:x + w]
    t = _load_dialog_template().get("t")
    if t is None or sub.shape[0] < t.shape[0] or sub.shape[1] < t.shape[1]:
        return None, 0.0
    score = float(cv2.matchTemplate(sub, t, cv2.TM_CCOEFF_NORMED).max())
    return ("V" if score >= 0.80 else None), score


DIR_BTN_BOX = (1124, 556, 84, 84)   # 右下「顺/逆」菱形按钮的可点框（实测）
DIR_TEMPLATES = {"顺": "迷踪盘.方向顺.png", "逆": "迷踪盘.方向逆.png"}

_DIR_CACHE: dict = {}


def _load_dir_templates():
    if _DIR_CACHE:
        return _DIR_CACHE
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "resource" / "image"
    for name, fn in DIR_TEMPLATES.items():
        p = root / fn
        if p.exists():
            t = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
            if t is not None:
                _DIR_CACHE[name] = t
    return _DIR_CACHE


def read_dir(img) -> tuple:
    """读右下按钮当前指向：('顺'|'逆'|None, 分数)。

    为什么不用 OCR：单字「顺/逆」在框架 OCR 里极不稳（同一 ROI 时出时不出，
    0.696 那种低分还带 0.3~0.4 噪声）。墨迹重心法也只能把两字差拉开到
    LR 0.838 vs 1.172，边界太窄。模板 NCC 实测分离干净：
    同字 1.0000，跨字 0.6472，所以取 max 且要求 >0.8 才有唯一解。
    """
    x, y, w, h = DIR_BTN_BOX
    sub = img[y:y + h, x:x + w]
    if sub.shape[0] != h or sub.shape[1] != w:
        return None, 0.0
    best, score = None, -1.0
    for name, t in _load_dir_templates().items():
        r = float(cv2.matchTemplate(sub, t, cv2.TM_CCOEFF_NORMED).max())
        if r > score:
            best, score = name, r
    if score < 0.80:
        return None, score
    return best, score


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
