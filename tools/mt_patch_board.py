# -*- coding: utf-8 -*-
"""给 迷踪盘.json 补「棋盘页」收尾出口节点。

背景（实测）：求解器若没真正通关，盘面停在棋盘页，而收尾循环原来的 5 个候选
（完成/退关卡页/退茶室/退派遣/大厅）在棋盘页全部 Recognition.Failed，
20s/40s 预算耗光后 Task timeout 收场。棋盘页左上角是「← 迷踪盘」返回箭头，
右下有「悔」按钮，用「悔」做页面断言最稳（跨页 NCC：棋盘 1.0000 / 关卡选择 0.2424）。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPE = ROOT / "resource" / "pipeline" / "迷踪盘.json"

NODE = {
    "desc": ("棋盘页出口：断言当前确实在棋盘上（右下「悔」按钮做模板，跨页 NCC "
             "棋盘 1.0000 / 关卡选择 0.2424），然后点左上返回箭头 (60,35) 回关卡选择页，"
             "把控制权交回收尾驱动节点。补这个节点的原因：求解器一旦没真正通关，"
             "盘面就停在棋盘页，而原来的 5 个收尾候选在这一页全部不匹配，只能等超时失败。"),
    "recognition": "TemplateMatch",
    "roi": [440, 540, 130, 110],
    "template": "迷踪盘.棋盘.png",
    "threshold": 0.8,
    "action": "Click",
    "target": [60, 35],
    "post_delay": 1800,
    "max_hit": 3,
    "next": ["迷踪盘.收尾"],
}


def main() -> int:
    d = json.loads(PIPE.read_text(encoding="utf-8"))
    bak = PIPE.with_suffix(f".json.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(PIPE, bak)

    d["迷踪盘.收尾.棋盘"] = NODE
    nxt = d["迷踪盘.收尾"]["next"]
    if "迷踪盘.收尾.棋盘" not in nxt:
        # 排在「完成」之后、「退关卡页」之前：完成遮罩优先，其次是棋盘页
        nxt.insert(1, "迷踪盘.收尾.棋盘")

    PIPE.write_text(json.dumps(d, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")

    # 静态校验
    tpl_dir = ROOT / "resource" / "image"
    missing, dangling = [], []
    for name, node in d.items():
        if not isinstance(node, dict):
            continue
        t = node.get("template")
        for tt in ([t] if isinstance(t, str) else (t or [])):
            if not (tpl_dir / tt).exists():
                missing.append((name, tt))
        for key in ("next", "on_error"):
            for tgt in node.get(key) or []:
                if tgt and tgt not in d and tgt != "ReturnHall":
                    dangling.append((name, key, tgt))

    print(f"备份 {bak.name}")
    print(f"nodes={len(d)}")
    print("收尾 next =", d["迷踪盘.收尾"]["next"])
    print("missing_templates =", missing)
    print("dangling =", dangling)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
