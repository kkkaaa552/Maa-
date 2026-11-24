"""19 格判据数值全量导出：定位「六边形被读成棋子」这类误判。

用法：python tools/mtdump.py debug/shots/now5.png

输出每格：坐标、内圈灰度均值、暗像素占比 dkFrac、R-B、G-R，并给出按当前常量
（PIECE_DKFRAC / HEX_RB / HEX_GR）的分类结果，便于对照截图人工裁决。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import mtboard as M  # noqa: E402


def imread_u(path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"读图失败: {path}")
    return img


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else str(ROOT / "debug" / "shots" / "now5.png")
    img = imread_u(path)
    b = M.read_board(img)
    print(f"scene={path}")
    print(f"读取结果: pieces={sorted(b['pieces'])} hexes={sorted(b['hexes'])} targets={sorted(b['targets'])}")
    print()
    print(f"{'cell':10s} {'x':>5s} {'y':>5s} {'mean':>7s} {'dkFrac':>7s} {'R-B':>7s} {'G-R':>7s}  {'分类':8s}")
    for c, (x, y) in sorted(M.CELLS.items(), key=lambda kv: (kv[1][1], kv[1][0])):
        mean, dk, rb, gr = b["stats"][c]
        if c in b["pieces"]:
            kind = "piece"
        elif c in b["hexes"]:
            kind = "hex"
        else:
            kind = "empty"
        print(f"{str(c):10s} {x:5.0f} {y:5.0f} {mean:7.1f} {dk:7.3f} {rb:7.1f} {gr:7.1f}  {kind}")
    print()
    print(f"常量: PIECE_DKFRAC={M.PIECE_DKFRAC} HEX_RB={M.HEX_RB} HEX_GR={M.HEX_GR}")
    print(f"棋子数={len(b['pieces'])} 目标数={len(b['targets'])} 差={len(b['pieces'])-len(b['targets'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
