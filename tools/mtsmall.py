"""小盘绿点精确测量：给出绿点质心、面积、中心亮度，区分「未解目标」与「已解笑脸」。

用法：python tools/mtsmall.py [图片路径]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import mtboard as M  # noqa: E402


def load(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def main(argv) -> int:
    p = argv[1] if len(argv) > 1 else str(ROOT / "debug" / "shots" / "dirtest.png")
    img = load(p)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (35, 25, 40), (95, 255, 180))
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
    print(f"# {p}\n小盘区域绿块（面积 200~2000）:")
    out = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA]
        if not (200 <= a <= 2000):
            continue
        cx, cy = cen[i]
        if not (150 <= cx <= 460 and 140 <= cy <= 430):
            continue
        best, bd = None, 1e9
        for c, (x, y) in M.SMALL.items():
            d = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if d < bd:
                bd, best = d, c
        # 中心 3x3 亮度：笑脸有白眼睛 -> 更亮
        sub = img[int(cy) - 2:int(cy) + 3, int(cx) - 2:int(cx) + 3]
        luma = float(cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY).mean())
        w = st[i, cv2.CC_STAT_WIDTH]
        h = st[i, cv2.CC_STAT_HEIGHT]
        print(f"  ({cx:7.1f},{cy:7.1f}) area={a:5d} wh={w}x{h} 中心亮度={luma:6.1f} "
              f"最近格 {str(best):9s} 距 {bd:5.1f}px")
        out.append(best if bd < 9 else None)
    print(f"\nread_targets_small = {sorted(M.read_targets_small(img))}")
    print(f"本脚本判定的格     = {sorted(x for x in out if x)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
