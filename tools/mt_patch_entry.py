"""把 迷踪盘.json 的入口段改成与收尾段同构的「驱动节点 + 页面断言」自持循环。

机制依据（实测，见 tools/mtlog.py 的取证）：
  1. next 列表的等待预算由**驱动节点**自己的 timeout 决定；候选节点自己写的
     timeout 不生效。所以「单候选 next + on_error 串联」必然耗光预算后 Task timeout。
  2. 因此把多个互斥的页面断言并进同一个 next 列表，每轮按序各试一次，
     当前页那个当轮命中，不白等；命中节点动作完成后 next 回驱动节点形成循环。
     死循环用 max_hit 兜，终止靠目标页断言。

为什么入口段也要这么改（实机失败取证）：
  茶室页那批「请调券 / 招募券」数量气泡是浮动的，与「迷」徽记相邻且会左右漂移。
  用固定坐标点「迷」会隔三差五点到气泡上，弹出物品浮窗把整页盖住，
  于是 关卡页 断言失败 → on_error 直接掉进收尾。改用 target:true 跟随模板命中位置，
  并额外给一个「点空白处关浮窗」的兜底候选。
"""
from __future__ import annotations

import glob
import json
import shutil
import time
from pathlib import Path

PIPE_DIR = Path(r"D:\mixian\my-maa\resource\pipeline")
IMG_DIR = Path(r"D:\mixian\my-maa\resource\image")
P = PIPE_DIR / "迷踪盘.json"


def main() -> int:
    bak = P.with_name(f"{P.stem}.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(P, bak)
    d = json.loads(P.read_text(encoding="utf-8"))

    # ── 入口驱动节点 ────────────────────────────────────────────────
    d["迷踪盘"] = {
        "desc": "入口驱动节点：轮询「大厅 → 派遣公司 → 茶室 → 关卡选择」四级页面断言，"
                "当前在哪一级就做那一步。next 顺序即优先级：关卡页放最前，已经进了就不再乱点。"
                "末尾的「点空白」是兜底——气泡浮窗挡住页面时，先点一下空地把它关掉再重试。",
        "recognition": "DirectHit",
        "action": "DoNothing",
        "pre_delay": 500,
        "timeout": 45000,
        "next": ["迷踪盘.关卡页", "迷踪盘.点迷", "迷踪盘.点店", "迷踪盘.去派遣", "迷踪盘.点空白"],
        "on_error": ["迷踪盘.收尾"],
    }
    d["迷踪盘.去派遣"] = {
        "desc": "大厅右下「派遣公司」按钮（OCR）。roi 收在右上角，避免与派遣公司页左上角同名标题混淆。",
        "recognition": "OCR",
        "roi": [1080, 420, 200, 80],
        "expected": ["派遣公司"],
        "action": "Click",
        "post_delay": 2200,
        "max_hit": 4,
        "next": ["迷踪盘"],
    }
    d["迷踪盘.点店"] = {
        "desc": "派遣公司页左下那张「茶室」邮票招牌。模板实测：同屏 1.0，其他画面 ≤0.39。",
        "recognition": "TemplateMatch",
        "template": "迷踪盘.茶室.png",
        "roi": [900, 360, 380, 300],
        "threshold": 0.8,
        "action": "Click",
        "target": True,
        "post_delay": 2500,
        "max_hit": 4,
        "next": ["迷踪盘"],
    }
    d["迷踪盘.点迷"] = {
        "desc": "茶室页圆形徽记上的「迷」（迷踪盘入口）。实测同屏 1.0、其他画面 ≤0.39。"
                "必须 target:true 跟随命中位置——徽记与旁边数量气泡都会浮动，固定坐标会点到气泡上。",
        "recognition": "TemplateMatch",
        "template": "迷踪盘.入口.png",
        "roi": [380, 150, 260, 250],
        "threshold": 0.8,
        "action": "Click",
        "target": True,
        "post_delay": 2200,
        "max_hit": 6,
        "next": ["迷踪盘"],
    }
    d["迷踪盘.点空白"] = {
        "desc": "兜底：点茶室页左下空地，关掉误点开的物品浮窗（请调券/招募券）。"
                "排在其他候选之后，只有前面全不命中才会执行。",
        "recognition": "DirectHit",
        "action": "Click",
        "target": [200, 650],
        "post_delay": 700,
        "max_hit": 4,
        "next": ["迷踪盘"],
    }

    # ── 关卡选择阶段 ───────────────────────────────────────────────
    d["迷踪盘.关卡页"] = {
        "desc": "关卡选择页就绪判据兼断言：左上第一张「体验关卡」卡片标签，恒在。"
                "实测本页 0.9889 / 茶室页 0.6263 / 棋盘 0.4995，阈值 0.8 分得开。",
        "recognition": "TemplateMatch",
        "template": "迷踪盘.体验关卡.png",
        "roi": [150, 290, 190, 80],
        "threshold": 0.8,
        "action": "DoNothing",
        "next": ["迷踪盘.选关"],
        "on_error": ["迷踪盘"],
    }
    d["迷踪盘.选关"] = {
        "desc": "点关卡卡片。target 默认「体验关卡」(231,270)，option「迷踪盘关卡」会覆盖这里。"
                "点卡片只是选中，不会直接进关。",
        "recognition": "DirectHit",
        "action": "Click",
        "target": [231, 270],
        "post_delay": 1200,
        "max_hit": 4,
        "next": ["迷踪盘.前往"],
        "on_error": ["迷踪盘.关卡页"],
    }
    d["迷踪盘.前往"] = {
        "desc": "选中卡片后出现的红色「前往」条（模板 222x41，同屏 1.0）。点它才真正进棋盘。",
        "recognition": "TemplateMatch",
        "template": "迷踪盘.前往.png",
        "roi": [700, 460, 380, 130],
        "threshold": 0.8,
        "action": "Click",
        "target": True,
        "post_delay": 3000,
        "next": ["迷踪盘.求解"],
        "on_error": ["迷踪盘.选关"],
    }

    P.write_text(json.dumps(d, ensure_ascii=False, indent=4) + "\n", encoding="utf-8", newline="\n")

    # ── 静态校验 ───────────────────────────────────────────────────
    nodes = [k for k in d if not k.startswith("$")]
    missing = sorted({d[k]["template"] for k in nodes if isinstance(d[k].get("template"), str)
                      and not (IMG_DIR / d[k]["template"]).exists()})
    all_nodes: set[str] = set()
    for f in glob.glob(str(PIPE_DIR / "*.json")):
        all_nodes |= {k for k in json.loads(Path(f).read_text(encoding="utf-8")) if not k.startswith("$")}
    dangling = [(k, key, t) for k in nodes for key in ("next", "on_error")
                for t in (d[k].get(key) or []) if isinstance(t, str) and t not in all_nodes]
    orphan = [k for k in nodes if k != "迷踪盘"
              and not any(k in (d[o].get(key) or []) for o in nodes for key in ("next", "on_error"))]
    print(f"backup={bak.name}")
    print(f"nodes={len(nodes)} missing_templates={missing} dangling={dangling} orphan={orphan}")
    for k in nodes:
        print(f"  {k:14s} next={d[k].get('next')} on_error={d[k].get('on_error')} max_hit={d[k].get('max_hit')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
