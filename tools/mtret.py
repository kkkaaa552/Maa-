# -*- coding: utf-8 -*-
"""统计 maafw.log 里每个任务的收尾结果（entry + ret），按时间序打印最后 N 条。

用法：
    python tools/mtret.py [N]
注意：含引号与正则，必须落成 .py 再跑——PowerShell 的 here-string 会吞引号。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "debug" / "maafw.log"

RE_ENTRY = re.compile(r'"entry":"([^"]+)"')
RE_RET = re.compile(r"\[ret=(\w+)\]")


def main(argv):
    n = int(argv[1]) if len(argv) > 1 else 8
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = []
    for ln in lines:
        if "task end" not in ln or '"entry"' not in ln:
            continue
        m, r = RE_ENTRY.search(ln), RE_RET.search(ln)
        if not m:
            continue
        rows.append((ln[1:20], m.group(1), r.group(1) if r else "?"))
    print(f"共 {len(rows)} 条 task end")
    for row in rows[-n:]:
        print("  ", row[0], row[1], "ret=" + row[2])
    c = Counter(r[2] for r in rows[-n:])
    print("最近", n, "条 ret 分布：", dict(c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
