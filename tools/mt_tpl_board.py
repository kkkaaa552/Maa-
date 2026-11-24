# -*- coding: utf-8 -*-
"""从棋盘页截图裁「悔」按钮做棋盘页模板，并做跨页 NCC 分离度检查。

用法：
    python tools/mt_tpl_board.py <board_shot> <other_shot> [other2 ...]
说明：模板落 resource/image/迷踪盘.棋盘.png；对每张图打印全画面 NCC 峰值与位置。
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "resource" / "image" / "迷踪盘.棋盘.png"
CROP = (464, 556, 538, 630)  # x0,y0,x1,y1  「悔」菱形按钮


def rd(p):
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def main(argv):
    if len(argv) < 2:
        print("用法: mt_tpl_board.py <board_shot> [other ...]")
        return 2
    img = rd(argv[1])
    x0, y0, x1, y1 = CROP
    tpl = img[y0:y1, x0:x1]
    TPL.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", tpl)[1].tofile(str(TPL))
    print(f"模板 {TPL.name} {tpl.shape[1]}x{tpl.shape[0]} <- {argv[1]}[{y0}:{y1},{x0}:{x1}]")

    for p in argv[1:]:
        scene = rd(p)
        res = cv2.matchTemplate(scene, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        # 次峰：把峰值附近压掉后再取最大，判断模板是否过泛
        r2 = res.copy()
        pad = 40
        cx, cy = loc
        r2[max(0, cy - pad):cy + pad, max(0, cx - pad):cx + pad] = -1
        _, mx2, _, loc2 = cv2.minMaxLoc(r2)
        print(f"{Path(p).name:20s} 峰值 {mx:.4f} at {loc}  次峰 {mx2:.4f} at {loc2}"
              f"  margin {mx - mx2:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
