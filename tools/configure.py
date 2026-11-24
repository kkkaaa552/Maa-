#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""configure.py — 校验 / 补齐 MaaFramework 的 OCR 模型。

官方模板里 tools/configure.py 干的是「从 download.maafw.xyz 下 ppocr 模型」。
本项目的模型已经就位（resource/model/ocr/ 三件套），所以这里改成两件事：

  1. 校验：det.onnx / rec.onnx / keys.txt 三件齐全、大小与已知值对得上；
  2. 缺失时：从官方地址下载（若允许联网），否则报错退出。

用法（CI 里 install 之前跑一次；本地也可单独跑）：

    python tools/configure.py            # 校验，缺了才下载
    python tools/configure.py --check    # 只校验，不联网
    python tools/configure.py --force    # 强制重下

退出码：0 = 三件齐且大小正确；非 0 = 缺文件或大小不符。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OCR_DIR = ROOT / "resource" / "model" / "ocr"

# 来自 D:\mixian\08-OCR模型\说明.md 的实测记录（PP-OCRv6_small，2026/06/12 打包）。
# 只校验大小，不校验 sha256：keys.txt 在历史上有过两次不同字符表版本，
# 硬编 sha 会让升级模型时莫名其妙失败；大小足以挡住「下载被截断」这类真实事故。
EXPECT = {
    "det.onnx": 9_893_172,
    "rec.onnx": 21_146_753,
    "keys.txt": 74_947,
}

MODEL_URL = "https://download.maafw.xyz/MaaCommonAssets/OCR/ppocr_v6/ppocr_v6-small.zip"
MODEL_ZIP_SIZE = 26_597_024


def check(verbose: bool = True) -> list[str]:
    """返回问题列表；空列表表示全部正常。"""
    problems: list[str] = []
    if not OCR_DIR.is_dir():
        problems.append(f"OCR 目录不存在: {OCR_DIR}")
        return problems

    for name, want in EXPECT.items():
        p = OCR_DIR / name
        if not p.is_file():
            problems.append(f"缺文件: {name}")
            continue
        got = p.stat().st_size
        if got != want:
            problems.append(f"{name} 大小不符: {got} != {want}（可能下载被截断）")
        elif verbose:
            print(f"  OK  {name:<12} {got:>12,} B")

    # README.md 不是必需，有就顺手提一句
    if verbose and (OCR_DIR / "README.md").is_file():
        print(f"  --  README.md 存在（非必需）")
    return problems


def download_and_unpack(force: bool = False) -> int:
    """从官方地址下载解压。返回退出码。"""
    if not force:
        problems = check(verbose=False)
        if not problems:
            print("[configure] OCR 模型已就绪，跳过下载。")
            return 0

    print(f"[configure] 下载 {MODEL_URL}")
    tmp = Path(tempfile.mkdtemp(prefix="ppocr_"))
    try:
        zip_path = tmp / "ppocr_v6-small.zip"
        urllib.request.urlretrieve(MODEL_URL, zip_path)
        size = zip_path.stat().st_size
        print(f"[configure] 下载完成 {size:,} B")
        if size != MODEL_ZIP_SIZE:
            print(
                f"[configure] 警告：大小 {size:,} 与已知值 {MODEL_ZIP_SIZE:,} 不符，"
                "继续解压但请留意模型是否完整。",
                file=sys.stderr,
            )

        OCR_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            # zip 里通常是 ppocr_v6/ 一层，把三件套平铺到 OCR_DIR
            for member in zf.namelist():
                base = Path(member).name
                if base in EXPECT:
                    with zf.open(member) as src, (OCR_DIR / base).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"  解开 {base}")
                elif base == "README.md":
                    with zf.open(member) as src, (OCR_DIR / base).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    problems = check(verbose=True)
    if problems:
        for p in problems:
            print(f"[configure] 失败: {p}", file=sys.stderr)
        return 1
    print("[configure] OCR 模型就绪。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="校验 / 补齐 OCR 模型")
    ap.add_argument("--check", action="store_true", help="只校验，不联网")
    ap.add_argument("--force", action="store_true", help="强制重新下载")
    args = ap.parse_args()

    print(f"[configure] OCR 目录: {OCR_DIR}")
    if args.check:
        problems = check(verbose=True)
        if problems:
            for p in problems:
                print(f"  FAIL  {p}", file=sys.stderr)
            return 1
        print("[configure] 三件套齐全。")
        return 0

    return download_and_unpack(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
