"""删除 迷踪盘.json 里被新驱动架构淘汰的孤儿节点，并做完整静态校验。

淘汰原因：入口段改成「迷踪盘 驱动节点 + 页面断言/点击候选」后，
旧的串联节点 `迷踪盘.茶室`、`迷踪盘.入口` 已无人引用（orphan），留着只会让
bundle 里多两个没有任何路径能到达的节点。
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
DROP = ("迷踪盘.茶室", "迷踪盘.入口")


def main() -> int:
    bak = P.with_name(f"{P.stem}.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(P, bak)
    d = json.loads(P.read_text(encoding="utf-8"))
    removed = [k for k in DROP if k in d]
    for k in removed:
        del d[k]
    P.write_text(json.dumps(d, ensure_ascii=False, indent=4) + "\n", encoding="utf-8", newline="\n")

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
    no_next = [k for k in nodes if not (d[k].get("next") or []) and k != "迷踪盘.收尾.大厅"]

    print(f"backup={bak.name} removed={removed}")
    print(f"nodes={len(nodes)} missing_templates={missing}")
    print(f"dangling={dangling}")
    print(f"orphan={orphan}")
    print(f"terminal_without_next(除大厅外应为空)={no_next}")
    print(f"ReturnHall 引用={[k for k in nodes if any('ReturnHall' in (d[k].get(x) or []) for x in ('next','on_error'))]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
