"""模板定位核查：在给定截图上做全画面 NCC，打印每个模板的峰值与次峰位置。

用法：
    python tools/mtchk.py debug/shots/xxx.png                # 默认查 迷踪盘.*.png
    python tools/mtchk.py debug/shots/xxx.png 迷踪盘.入口.png  # 只查指定模板

输出每行：模板名  score=峰值  at=(x,y,w,h)  runner=次峰值位置
次峰用于判断模板是否"长得太通用"（多个位置都高分 = 不适合当页面判据）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

IMG_DIR = ROOT / "resource" / "image"


def imread_u(path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"读图失败: {path}")
    return img


def peaks(scene: np.ndarray, tpl: np.ndarray, n: int = 3) -> list[tuple[float, tuple[int, int]]]:
    res = cv2.matchTemplate(scene, tpl, cv2.TM_CCOEFF_NORMED)
    out: list[tuple[float, tuple[int, int]]] = []
    r = res.copy()
    h, w = tpl.shape[:2]
    for _ in range(n):
        _, mx, _, ml = cv2.minMaxLoc(r)
        out.append((float(mx), (ml[0], ml[1])))
        x0, y0 = ml
        cv2.rectangle(r, (max(0, x0 - w // 2), max(0, y0 - h // 2)),
                      (min(r.shape[1] - 1, x0 + w // 2), min(r.shape[0] - 1, y0 + h // 2)),
                      -1.0, -1)
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    scene = imread_u(argv[1])
    names = argv[2:] or [p.name for p in sorted(IMG_DIR.glob("迷踪盘.*.png"))]
    print(f"scene={argv[1]}  {scene.shape[1]}x{scene.shape[0]}")
    for name in names:
        tpl = imread_u(IMG_DIR / name)
        h, w = tpl.shape[:2]
        ps = peaks(scene, tpl)
        best, bp = ps[0]
        second, sp = ps[1] if len(ps) > 1 else (0.0, (0, 0))
        print(f"  {name:22s} score={best:.4f} at=({bp[0]},{bp[1]},{w},{h}) "
              f"2nd={second:.4f} at={sp}  margin={best - second:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
