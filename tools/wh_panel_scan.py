"""仓库面板扫描 v2：点开详情面板 → 全图 OCR → 解析名称/库存 → 用图鉴名做纠错。

用法:
    python tools/wh_panel_scan.py --cat 票券 --rows 3 [--limit N] [--dry]

输出: data/warehouse/<cat>.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "tools"))
import wh_counts  # noqa: E402
import wh_scan  # noqa: E402

ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
SERIAL = "127.0.0.1:16384"
OCR_PS1 = ROOT / "tools" / "win_ocr.ps1"
SHOT_DIR = ROOT / "debug" / "shots"
IMAGE_DIR = ROOT / "resource" / "image"

X0, Y0, PX, PY, CW, CH = 365, 130, 150, 128, 100, 100
COLS = 6


def adb(*args: str) -> str:
    return subprocess.run([ADB, "-s", SERIAL, "shell", *args],
                          capture_output=True, text=True, timeout=30).stdout


def screencap(path: Path) -> bool:
    adb("screencap", "-p", "/sdcard/_wh.png")
    subprocess.run([ADB, "-s", SERIAL, "pull", "/sdcard/_wh.png", str(path)],
                   capture_output=True, timeout=60)
    return path.exists() and path.stat().st_size > 1000


def ocr_rows(path: Path) -> list[tuple[int, int, str]]:
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(OCR_PS1), "-Path", str(path)],
                       capture_output=True, timeout=120)
    rows = []
    for ln in r.stdout.decode("utf-8", errors="replace").splitlines():
        if "\t" not in ln:
            continue
        head, txt = ln.split("\t", 1)
        m = re.match(r"\((\d+),(\d+)\)", head)
        if not m:
            continue
        rows.append((int(m.group(1)), int(m.group(2)), txt.replace(" ", "")))
    return rows


PUNCT = set("。，、|·（）()：:；;《》")


def parse_panel(rows: list[tuple[int, int, str]]) -> tuple[str, str]:
    name, stock = "", ""
    for x, y, t in sorted(rows, key=lambda r: (r[1], r[0])):
        if "存" not in t:
            continue
        d = re.sub(r"[^\d.万]", "", t)
        if d:
            stock = d
            continue
        # 标签与数值被 OCR 拆成两行：取同一高度、右侧那段纯数字
        for x2, y2, t2 in rows:
            if x2 > x and abs(y2 - y) <= 8 and re.fullmatch(r"[\d.万]+", t2):
                stock = t2
                break
        if stock:
            continue
    # 库存只认含「存」的行；没有就留空，交给像素数量兜底（顶栏计数极易误抓）
    for x, y, t in sorted(rows, key=lambda r: (r[1], r[0])):
        if not (355 <= x <= 700 and 150 <= y <= 258):
            continue
        if "存" in t or "使用" in t or "获取" in t:
            continue
        if any(ch in PUNCT for ch in t):
            continue
        if not (2 <= len(t) <= 8):
            continue
        name = t
        break
    return name, stock


def catalog() -> list[str]:
    names = []
    for p in sorted(IMAGE_DIR.glob("*.png")):
        g = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
        if g is None:
            continue
        h, w = g.shape[:2]
        if h == w and h >= 90:
            names.append(p.stem)
    return names


def normalize_name(t: str) -> str:
    """把 OCR 出来的拉丁罗马数字、被读成「一」的连字符，统一成图鉴写法。"""
    t = t.strip()
    roman = {"Ⅰ": "i", "Ⅱ": "ii", "Ⅲ": "iii", "Ⅳ": "iv", "Ⅴ": "v"}
    for u, a in roman.items():
        t = t.replace(u, a)
    tail = re.match(r"^(.*?)([ivIVl]{1,4})$", t)
    if tail:
        body, suf = tail.group(1), tail.group(2).lower().replace("l", "i")
        if suf in ("i", "ii", "iii", "iv", "v"):
            t = body + suf.upper()
    # 「颜料一矿物颜料」这种：中间的「一」其实是连字符
    m = re.match(r"^(\S{1,4})一(\S{1,8})$", t)
    if m:
        t = m.group(1) + "-" + m.group(2)
    return t


def correct(raw: str, names):
    """返回 (最接近的图鉴名, 相似度, 是否可信)。可信才替换。"""
    if not raw:
        return raw, 0.0, False
    cand = normalize_name(raw)
    best, best_sim = raw, 0.0
    for n in names:
        r = SequenceMatcher(None, cand.replace(" ", ""), normalize_name(n).replace(" ", "")).ratio()
        if r > best_sim:
            best, best_sim = n, r
    return best, round(best_sim, 3), best_sim >= 0.75

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", default="票券")
    ap.add_argument("--rows", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    names = catalog()
    tmp = SHOT_DIR / "_panel_tmp.png"
    if not screencap(tmp):
        print("截图失败")
        return 1
    color0 = cv2.imdecode(np.fromfile(str(tmp), dtype=np.uint8), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(color0, cv2.COLOR_BGR2GRAY)
    bank = wh_counts.build(wh_counts.TRAIN)
    icons = wh_scan.load_icons()

    cells, done = [], 0
    for r in range(args.rows):
        for c in range(COLS):
            x, y = X0 + c * PX, Y0 + r * PY
            cnt = "".join(wh_counts.classify(g, bank)[0] for _, g in wh_counts.glyphs(gray, r, c))
            dark = float((gray[y:y + CH, x:x + CW] < 70).mean())
            rec = {"row": r, "col": c, "count_px": cnt}
            if dark < 0.15 and not cnt:
                rec["status"] = "empty"
                cells.append(rec)
                continue
            if args.dry:
                rec["status"] = "occupied"
                cells.append(rec)
                continue
            if args.limit and done >= args.limit:
                break
            done += 1
            pre = SHOT_DIR / "_panel_pre.png"
            screencap(pre)
            pre_bytes = pre.read_bytes() if pre.exists() else b""
            adb("input", "tap", str(x + CW // 2), str(y + CH // 2))
            panel = SHOT_DIR / "_panel_full.png"
            rows: list[tuple[int, int, str]] = []
            for wait in (1.2, 0.8, 0.8):
                time.sleep(wait)
                if not screencap(panel):
                    continue
                if panel.read_bytes() == pre_bytes:      # 画面没变，再等
                    continue
                rows = ocr_rows(panel)
                if any("存" in t for _, _, t in rows):
                    break
            raw_name, stock = parse_panel(rows)
            for _ in range(2):                   # 标题空 → 重新截图再认
                if raw_name:
                    break
                time.sleep(0.7)
                if not screencap(panel):
                    continue
                rows = ocr_rows(panel)
                raw_name, stock = parse_panel(rows)
            if not raw_name:                     # 标题没读到 → 放大面板区再认一次
                big = SHOT_DIR / "_panel_big.png"
                img = cv2.imdecode(np.fromfile(str(panel), dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    sub = img[140:360, 440:900]
                    sub = cv2.resize(sub, None, fx=2.2, fy=2.2, interpolation=cv2.INTER_CUBIC)
                    cv2.imencode(".png", sub)[1].tofile(str(big))
                    n2, s2 = parse_panel(ocr_rows(big))
                    raw_name = raw_name or n2
                    stock = stock or s2
            fixed, sim, good = correct(raw_name, names)
            name_final, src = (fixed, "ocr") if (raw_name and good) else (raw_name, "ocr")
            icon_score = icon_margin = 0.0
            if not name_final:                      # OCR 没读到 → 图标匹配兜底
                g2, h2 = wh_scan.cell_patch(color0, r, c)
                icon_name, icon_score, icon_margin = wh_scan.identify(g2, h2, icons)
                name_final, src = icon_name, "icon"
                unknown_icon = icon_score < 0.72 or icon_margin < 0.06
            else:
                unknown_icon = False
            # 库存与像素不一致 → 补拍一次再判（面板偶尔还停留在上一格）
            if stock and cnt and stock != cnt:
                time.sleep(0.9)
                if screencap(panel):
                    n2, s2 = parse_panel(ocr_rows(panel))
                    if s2 and s2 == cnt:
                        stock, raw_name = s2, (n2 or raw_name)
            rec.update(status="panel", name_raw=raw_name, name=name_final, name_source=src,
                       sim=sim, icon_score=round(icon_score, 3), icon_margin=round(icon_margin, 3),
                       stock=stock, count_final=(stock or cnt),
                       count_agree=(stock == cnt or not stock), count_conflict=(bool(stock) and bool(cnt) and stock != cnt))
            cells.append(rec)
            print(f'r{r}c{c} px={cnt!r:>8} 名称={rec["name"]!r}[{src}] '
                  f'sim={sim} icon={icon_score:.3f}/{icon_margin:.3f} 库存={rec["count_final"]!r}')
            adb("input", "keyevent", "4")
            time.sleep(0.85)

    out = ROOT / "data" / "warehouse" / f"{args.cat}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "category": args.cat, "cells": cells},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("写出", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
