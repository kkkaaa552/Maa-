# -*- coding: utf-8 -*-
"""把三份图鉴 xlsx 的 icons 目录同步进 resource/image/物品，供仓库识别使用。

用法：
    python tools/sync_icons.py            # 补齐缺失，不覆盖已存在的
    python tools/sync_icons.py --check    # 只检查，不动文件
    python tools/sync_icons.py --force    # 同名也覆盖（会用新图替换旧图）

为什么需要这个脚本：`wh_scan.load_icons()` 只 glob `resource/image/物品/*.png`，
物品名字与图标文件名必须一一对应。图鉴 xlsx 更新后，新物品的图标不在这个目录里，
识别池就没有它，NCC 只能从旧模板里挑最像的 —— 表现为「名字错得离谱但分数不低」。
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "resource" / "image" / "物品"
# 默认用项目自带的图鉴副本；--src 可指向新的导出目录
SRC_ROOT = ROOT / "data" / "refbook"
CATS = ("物料", "资源", "票券")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def collect(src_root: Path) -> dict[str, list[Path]]:
    """返回 {stem: [路径, ...]}，同名出现在多个分类时都留下。"""
    out: dict[str, list[Path]] = {}
    for cat in CATS:
        d = src_root / f"物华弥新-{cat}" / "icons"
        if not d.is_dir():
            print(f"[警告] 源目录不存在：{d}")
            continue
        for p in sorted(d.glob("*.png")):
            out.setdefault(p.stem, []).append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不改文件")
    ap.add_argument("--force", action="store_true", help="同名异字节也覆盖")
    ap.add_argument("--src", default=str(SRC_ROOT), help="图鉴导出根目录，默认用项目自带的 data\\refbook")
    args = ap.parse_args()

    if not ICON_DIR.is_dir():
        print(f"[错误] 图标目录不存在：{ICON_DIR}")
        return 2

    src_root = Path(args.src)
    if not src_root.is_dir():
        print(f"[错误] 源根目录不存在：{src_root}")
        return 2
    src = collect(src_root)
    if not src:
        print(f"[错误] 一个源图标都没找到，检查 {src_root}")
        return 2

    dup = {n: v for n, v in src.items() if len(v) > 1}
    if dup:
        print(f"[提示] {len(dup)} 个名字在多个分类里重复出现（内容相同则无害）:")
        for n, v in list(dup.items())[:10]:
            print("   ", n, "->", [str(x.parent.parent.name) for x in v])

    have = {p.stem: p for p in ICON_DIR.glob("*.png")}
    missing, same, differ = [], [], []
    for name, paths in sorted(src.items()):
        s = paths[0]
        if name not in have:
            missing.append((name, s))
        elif md5(have[name]) == md5(s):
            same.append(name)
        else:
            differ.append((name, s, have[name]))

    print(f"\n源 {len(src)} 个 | 目标 {len(have)} 个")
    print(f"  已一致   {len(same)}")
    print(f"  缺失     {len(missing)}")
    print(f"  同名异字节 {len(differ)}")

    for name, s in missing:
        print(f"    缺: {name}  ({s.stat().st_size} B) <- {s.parent.parent.name}")
    for name, s, t in differ:
        print(f"    异: {name}  源 {s.stat().st_size} B / 现有 {t.stat().st_size} B")

    only_local = sorted(set(have) - set(src))
    if only_local:
        print(f"\n只在目标目录有、图鉴里没有的 {len(only_local)} 个（不删，仅提示）:")
        print("   ", only_local)

    if args.check:
        print("\n[check 模式] 未改动任何文件")
        return 0 if not missing and not differ else 1

    n = 0
    for name, s in missing:
        shutil.copy2(s, ICON_DIR / s.name)
        n += 1
    if args.force:
        for name, s, t in differ:
            shutil.copy2(s, ICON_DIR / s.name)
            n += 1
    elif differ:
        print(f"\n[提示] {len(differ)} 个同名异字节未覆盖，确认要覆盖请加 --force")
    print(f"\n已写入 {n} 个文件到 {ICON_DIR}")
    print(f"目标目录现有 {len(list(ICON_DIR.glob('*.png')))} 个 png")
    return 0


if __name__ == "__main__":
    sys.exit(main())