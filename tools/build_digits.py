"""从仓库截图里切出数量数字，建立 0-9 字形模板。

用法:
    python tools/build_digits.py

输入: 若干张带已知数量的仓库截图 + 标签表（本文件内 LABELS）
输出: data/digits/d{0-9}.png  (28x20 二值模板) 与 data/digits/_report.txt
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SHOT_DIR = ROOT / "debug" / "shots"
OUT_DIR = ROOT / "data" / "digits"

# 网格几何（1280x720）
X0, Y0, PX, PY, CW, CH = 365, 130, 150, 128, 100, 100
# 数量数字所在相对格子的窗口
DX0, DX1 = 55, 108
DY0, DY1 = 86, 108

# 已知标注：只放稳定截图（切换分类后等 >=3s）
# 2026-11-24 13:56 重截重标（上一批 11/19 的图在 11/23 22:00 清目录时丢失）。
# 与 wh_counts.LABELS 保持一致；两边的切字几何都实测 48/48 格字数吻合。
LABELS = {
    "wh-06-material-stable-135653.png": {
        (0, 0): "1", (0, 1): "13", (0, 2): "1", (0, 3): "1", (0, 4): "12", (0, 5): "75",
        (1, 0): "20", (1, 1): "10", (1, 2): "92", (1, 3): "8", (1, 4): "57", (1, 5): "1",
        (2, 0): "1", (2, 1): "1", (2, 2): "2", (2, 3): "3", (2, 4): "104", (2, 5): "171",
        (3, 0): "25", (3, 1): "1", (3, 2): "19", (3, 3): "64", (3, 4): "45", (3, 5): "49",
    },
    "wh-07-resource-stable-135723.png": {
        (0, 0): "15", (0, 1): "27", (0, 2): "2", (0, 3): "4", (0, 4): "2", (0, 5): "18",
        (1, 0): "49", (1, 1): "5", (1, 2): "7", (1, 3): "10", (1, 4): "2", (1, 5): "1",
        (2, 0): "1", (2, 1): "1", (2, 2): "23", (2, 3): "2", (2, 4): "5", (2, 5): "38",
        (3, 0): "130", (3, 1): "29", (3, 2): "4", (3, 3): "365", (3, 4): "1", (3, 5): "2",
    },
}

THR = 150


def read_gray(p: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def cut_glyphs(shot: np.ndarray, r: int, c: int) -> list[np.ndarray]:
    x, y = X0 + c * PX, Y0 + r * PY
    reg = shot[y + DY0:y + DY1, x + DX0:x + DX1]
    mask = (reg > THR).astype(np.uint8) * 255
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    items = []
    for i in range(1, n):
        gx, gy, gw, gh, area = stats[i]
        if not (10 <= gh <= 17):
            continue
        if not (2 <= gw <= 14):
            continue
        if area < 18:
            continue
        items.append((int(gx), reg[gy:gy + gh, gx:gx + gw]))
    items.sort(key=lambda t: t[0])
    return [g for _, g in items]


def norm(g: np.ndarray) -> np.ndarray:
    return cv2.resize(g, (20, 28), interpolation=cv2.INTER_AREA)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[np.ndarray]] = {}
    stat_lines = []
    for name, labels in LABELS.items():
        shot = read_gray(SHOT_DIR / name)
        if shot is None:
            stat_lines.append(f"缺图: {name}")
            continue
        for (r, c), lab in labels.items():
            glyphs = cut_glyphs(shot, r, c)
            stat_lines.append(f"{name} ({r},{c}) 期望={lab} 切出={len(glyphs)}")
            if len(glyphs) != len(lab):
                continue
            for ch, g in zip(lab, glyphs):
                buckets.setdefault(ch, []).append(norm(g))

    for ch in sorted(buckets):
        stack = np.stack(buckets[ch]).astype(np.float32)
        cv2.imencode(".png", np.median(stack, axis=0).astype(np.uint8))[1].tofile(
            str(OUT_DIR / f"d{ch}.png"))
    report = "\n".join(
        ["每个数字的样本数: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(buckets.items())),
         "", *stat_lines])
    (OUT_DIR / "_report.txt").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
