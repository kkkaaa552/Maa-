"""解析 debug/maafw.log 尾段，按时间序打印节点识别/动作/转移事件。

用法：
    python tools/mtlog.py [行数]     默认扫最后 2000 行
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "debug" / "maafw.log"

NAME = re.compile(r'"name":"([^"]*)"')
DETAIL_NAME = re.compile(r'name=([^ \]]+)')
KEYEV = (
    "Node.Recognition.Succeeded",
    "Node.Recognition.Failed",
    "Node.Action.Succeeded",
    "Node.Action.Failed",
    "Node.PipelineNode.Succeeded",
    "Node.PipelineNode.Failed",
    "Node.NextList.Succeeded",
    "Node.NextList.Failed",
    "Node.Recognition.Starting",
    "Task timeout",
    "bad next",
    "invalid node id",
    "save on error",
)


def main(argv: list[str]) -> int:
    n = int(argv[1]) if len(argv) > 1 else 2000
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-n:]
    print(f"log 行数={len(lines)} 扫描尾段={len(tail)}")
    for l in tail:
        hit = [k for k in KEYEV if k in l]
        if not hit:
            continue
        ts = l[1:20]
        m = NAME.search(l)
        if not m:
            m = DETAIL_NAME.search(l)
        name = m.group(1) if m else ""
        extra = ""
        if "Task timeout" in l:
            d = re.search(r"duration_since\(start_clock\)=(\d+)ms", l)
            extra = f" {d.group(1)}ms" if d else ""
        if "bad next" in l:
            extra = " " + l[l.find("bad next"):][:80]
        print(f"{ts} {hit[0]:28s} {name}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
