"""数字字形模板的交叉验证：一页建模、另一页验证。

用法:
    python tools/wh_digits_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_digits import LABELS, SHOT_DIR, cut_glyphs, norm, read_gray  # noqa: E402


def build(source_files: list[str]) -> dict[str, np.ndarray]:
    buckets: dict[str, list[np.ndarray]] = {}
    for name in source_files:
        shot = read_gray(SHOT_DIR / name)
        for (r, c), lab in LABELS[name].items():
            glyphs = cut_glyphs(shot, r, c)
            if len(glyphs) != len(lab):
                continue
            for ch, g in zip(lab, glyphs):
                buckets.setdefault(ch, []).append(norm(g).astype(np.float32))
    return {k: np.median(np.stack(v), axis=0) for k, v in buckets.items()}


def classify(g: np.ndarray, tpl: dict[str, np.ndarray]) -> tuple[str, float, dict[str, float]]:
    q = norm(g).astype(np.float32)
    scores = {k: float(cv2.matchTemplate(q, v, cv2.TM_CCOEFF_NORMED)[0, 0]) for k, v in tpl.items()}
    best = max(scores, key=scores.get)
    return best, scores[best], scores


def run(src: list[str], dst: list[str]) -> None:
    tpl = build(src)
    total = ok = 0
    for name in dst:
        shot = read_gray(SHOT_DIR / name)
        for (r, c), lab in LABELS[name].items():
            glyphs = cut_glyphs(shot, r, c)
            got = "".join(classify(g, tpl)[0] for g in glyphs)
            total += 1
            hit = got == lab
            ok += hit
            if not hit:
                details = [classify(g, tpl) for g in glyphs]
                conf = " ".join(
                    f"{ch}->{b}({s:.2f})" for (b, s, _), ch in zip(details, lab))
                print(f"  错 {(r,c)} 期望={lab} 得到={got}  {conf}")
    print(f"{src[0]} 建模 → {dst[0]} 验证: {ok}/{total} = {ok / total:.1%}")


if __name__ == "__main__":
    a, b = "wh-06-material-stable-205724.png", "wh-07-resource-stable-205832.png"
    run([a], [b])
    print()
    run([b], [a])
