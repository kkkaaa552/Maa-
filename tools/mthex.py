"""用色彩掩膜求六边形质心，与 mtboard.CELLS 对照，检验格心表是否有半格偏移。

用法：python tools/mthex.py [图片路径]
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


def load(p: str):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def main(argv) -> int:
    p = argv[1] if len(argv) > 1 else str(ROOT / "debug" / "shots" / "dirtest.png")
    img = load(p)
    b, g, r = [img[..., i].astype(np.int16) for i in range(3)]
    mask = ((r - b > 45) & (g - r < -10)).astype(np.uint8) * 255
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
    print(f"# {p}")
    print("六边形质心（面积>=800）:")
    found = []
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < 800:
            continue
        cx, cy = cen[i]
        # 找最近的格心
        best, bd = None, 1e9
        for c, (x, y) in M.CELLS.items():
            d = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if d < bd:
                bd, best = d, c
        found.append(best)
        print(f"  ({cx:7.1f},{cy:7.1f}) area={st[i, cv2.CC_STAT_AREA]:5d} "
              f"最近格 {str(best):9s} 距离 {bd:5.1f}px  格心={M.CELLS[best]}")
    print(f"\nread_board 报的 hexes = {sorted(M.read_board(img)['hexes'])}")
    print(f"掩膜推出的 hexes      = {sorted(x for x in found if x)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
