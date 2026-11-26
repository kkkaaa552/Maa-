# -*- coding: utf-8 -*-
"""对一张实机截图，逐个迷踪盘模板算 roi 内与全屏 NCC，判阈值余量。

用法：python tools\mt_verify_tpl.py <png> [png...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "resource" / "image"

# (模板名, roi, 阈值) —— 与 resource/pipeline/迷踪盘.json 保持一致
CASES = [
    ("迷踪盘.体验关卡.png", [150, 290, 190, 80], 0.8),
    ("迷踪盘.前往.png", [700, 460, 380, 130], 0.8),
    ("迷踪盘.完成.png", [650, 320, 420, 220], 0.7),
    ("迷踪盘.棋盘.png", [440, 540, 130, 110], 0.8),
    ("迷踪盘.茶室.png", [900, 360, 380, 300], 0.8),
    ("迷踪盘.入口.png", [380, 150, 260, 250], 0.8),
    ("派遣公司.png", [1050, 390, 230, 190], 0.85),
]


def load(p) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for p in argv:
        img = load(p)
        print(f"\n=== {Path(p).name}  {img.shape[1]}x{img.shape[0]}")
        for name, roi, thr in CASES:
            tpl = load(IMG / name)
            x, y, w, h = roi
            sub = img[y:y + h, x:x + w]
            r = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, ml = cv2.minMaxLoc(r)
            r2 = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, fx, _, fl = cv2.minMaxLoc(r2)
            tag = "HIT" if mx >= thr else "   "
            print(f"  {name:20s} roi {mx:.4f} @({x + ml[0]},{y + ml[1]})  "
                  f"thr {thr}  {tag}   全屏 {fx:.4f} @{fl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
