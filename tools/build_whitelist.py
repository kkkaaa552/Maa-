# -*- coding: utf-8 -*-
"""从三份图鉴 xlsx 生成识别白名单 data/whitelist.json。

用户 2026-11-24 定：仓库识别**只识别**这三份名单里存在的名字与图标，名单外的一律不认。
名单来源固定为：
    D:\\新建文件夹\\物华弥新-物料\\物华弥新-物料.xlsx
    D:\\新建文件夹\\物华弥新-资源\\物华弥新-资源.xlsx
    D:\\新建文件夹\\物华弥新-票券\\物华弥新-票券.xlsx
sheet 名分别是「物料图鉴 / 资源图鉴 / 票券图鉴」，名称在第 4 列（D），图标文件名在第 9 列（I）。

外部路径只在**生成时**用一次，产物 data/whitelist.json 是项目内自包含的，
运行时 wh_scan.load_icons() 只读这个 JSON，不再依赖 D:\\新建文件夹 是否还在。

加名字的两种方式：
  1. 图鉴更新了 -> 重跑本脚本 `python tools/build_whitelist.py`
  2. 只是临时补一个图鉴没收录的（如「至纯灰雾涂料」「扯淡图章」）-> 往 EXTRA 里加一行
"""
from __future__ import annotations

import json
import pathlib

from openpyxl import load_workbook

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "whitelist.json"

XLSX_ROOT = pathlib.Path(r"D:\新建文件夹")
CATS = ("物料", "资源", "票券")

# 图鉴名单之外、用户点名要识别的物品。值 = 归属分类（决定它出现在报告的哪一节）。
# 加这里的名字同时要在 resource/image/物品/ 下放一张同名 png，否则没有模板可匹配。
EXTRA = {
    "至纯灰雾涂料": "物料",   # 用户 2026-11-24 指认：物料页那格红框礼盒其实是它
    "扯淡图章": "票券",       # 用户 2026-11-24 指认：之前误报成「颜料-矿物颜料」，实际属票券
}

# 图鉴里没有、也不参与识别的名字：写在这里是为了让脚本能核对「图标库多出来的是什么」，
# 而不是让它们被静默收进白名单。
DENY = {
    "改名卡": "用户 2026-11-24 点名不认（物料页那格）；图鉴也未收录",
    "冬谷币2": "旧版从截图抠的重复图标，图鉴里只有「冬谷币」；别名表已把 2 合并过去",
}


def read_cat(cat: str) -> tuple[list[str], list[str]]:
    """读一份 xlsx，返回 (名称列表, 图标文件名列表)，两者一一对应。"""
    p = XLSX_ROOT / f"物华弥新-{cat}" / f"物华弥新-{cat}.xlsx"
    if not p.is_file():
        raise FileNotFoundError(f"图鉴缺失: {p}")
    wb = load_workbook(p, data_only=True)
    ws = wb[wb.sheetnames[0]]
    names, files = [], []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 4 or not row[3]:
            continue
        names.append(str(row[3]).strip())
        files.append(str(row[8]).strip() if len(row) > 8 and row[8] else "")
    wb.close()
    return names, files


def main() -> None:
    cats: dict[str, list[str]] = {}
    problems: list[str] = []
    for cat in CATS:
        names, files = read_cat(cat)
        cats[cat] = names
        for n, f in zip(names, files):
            if not f:
                problems.append(f"{cat}/{n}: 图鉴没填图标文件名")
            elif pathlib.Path(f).stem != n:
                problems.append(f"{cat}/{n}: 图标文件名 {f!r} 与名称不一致")

    for n, cat in EXTRA.items():
        if n in cats.get(cat, []):
            problems.append(f"EXTRA/{n}: 图鉴里已有，可以从 EXTRA 删掉")
        else:
            cats.setdefault(cat, []).append(n)

    allnames = [n for c in CATS for n in cats[c]]
    if len(allnames) != len(set(allnames)):
        dup = [n for n in set(allnames) if allnames.count(n) > 1]
        problems.append(f"跨分类重名: {sorted(dup)}")

    icon_dir = ROOT / "resource" / "image" / "物品"
    stems = {p.stem for p in icon_dir.glob("*.png")}
    missing_icon = sorted(set(allnames) - stems - set(DENY))
    extra_icon = sorted(stems - set(allnames) - set(DENY))

    data = {
        "version": 1,
        "note": "仓库识别白名单：只认这里列出的名字。由 tools/build_whitelist.py 生成，别手改。",
        "source": [str(XLSX_ROOT / f"物华弥新-{c}" / f"物华弥新-{c}.xlsx") for c in CATS],
        "cats": {c: sorted(cats[c]) for c in CATS},
        "extra_reason": EXTRA,
        "deny": DENY,
        "counts": {c: len(cats[c]) for c in CATS},
        "total": len(allnames),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"写出 {OUT}  {OUT.stat().st_size:,} B")
    print("  分类数量: " + "  ".join(f"{c}={len(cats[c])}" for c in CATS))
    print(f"  合计 {len(allnames)} 名（含 EXTRA {len(EXTRA)} 个）")
    print(f"  图标库 {icon_dir} 共 {len(stems)} 个 png")

    if missing_icon:
        print(f"\n  [警告] 白名单有名字但图标库没有 ({len(missing_icon)})：")
        for n in missing_icon:
            print(f"     {n}")
    if extra_icon:
        print(f"\n  [提示] 图标库有、白名单不收 ({len(extra_icon)})：")
        for n in extra_icon:
            print(f"     {n}   ({DENY.get(n, '未在 DENY 登记')})")
    if problems:
        print(f"\n  [问题] {len(problems)} 条:")
        for s in problems:
            print(f"     {s}")
    else:
        print("\n  校验通过：名单 <-> 图标文件一一对应，无跨分类重名。")


if __name__ == "__main__":
    main()
