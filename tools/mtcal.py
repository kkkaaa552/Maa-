"""判据阈值标定：对若干张盘面截图，导出每格在多档灰度阈值下的暗像素占比，
用于确定「六边形 vs 棋子」的分界。

用法：python tools/mtcal.py debug/shots/a.png debug/shots/b.png ...
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import mtboard as M  # noqa: E402

THRESHOLDS = (110, 120, 130, 140, 150, 160, 170)


def imread_u(path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"读图失败: {path}")
    return img


def cell_stats(img, x, y):
    img_f = img.astype(np.float32)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    xi, yi = int(x), int(y)
    S = 20
    sub = img_f[yi - S:yi + S, xi - S:xi + S]
    gs = gray[yi - S:yi + S, xi - S:xi + S]
    yy, xx = np.mgrid[0:2 * S, 0:2 * S]
    m = np.sqrt((xx - S) ** 2 + (yy - S) ** 2) <= 11
    b, g, r = sub[..., 0][m], sub[..., 1][m], sub[..., 2][m]
    fr = {t: float((gs[m] < t).mean()) for t in THRESHOLDS}
    return float(gs[m].mean()), fr, float(r.mean() - b.mean()), float(g.mean() - r.mean())


def main(argv: list[str]) -> int:
    paths = argv[1:] or [str(ROOT / "debug" / "shots" / "now5.png")]
    hdr = "".join(f"{'dk<'+str(t):>7s}" for t in THRESHOLDS)
    for p in paths:
        img = imread_u(p)
        b = M.read_board(img)
        label = {}
        for c in b["pieces"]:
            label[c] = "piece(旧判)"
        for c in b["hexes"]:
            label[c] = "hex(旧判)"
        print(f"\n=== {p} ===")
        print(f"{'cell':9s} {'mean':>6s}{hdr} {'R-B':>6s} {'G-R':>6s}  旧判")
        rows = []
        for c, (x, y) in sorted(M.CELLS.items(), key=lambda kv: (kv[1][1], kv[1][0])):
            mean, fr, rb, gr = cell_stats(img, x, y)
            if mean > 200 and rb < 40:
                continue  # 空格不打印，减少噪声
            rows.append((c, mean, fr, rb, gr, label.get(c, "-")))
        for c, mean, fr, rb, gr, lab in rows:
            line = f"{str(c):9s} {mean:6.1f}" + "".join(f"{fr[t]:7.3f}" for t in THRESHOLDS)
            print(f"{line} {rb:6.1f} {gr:6.1f}  {lab}")
        # 分隔判据：R-B 与 G-R 的联合分布
        print(f"  空格 R-B 上限约 30；六边形候选需同时 R-B>45 且 G-R<-10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
