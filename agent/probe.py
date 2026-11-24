"""Agent 探针：Client 启动本进程时到底传了什么。

只落盘、不改任何东西，用来确定 MFAAvalonia 传 identifier 的方式与注入的 PI_* 变量。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
LOG = ROOT / "debug" / "agent_probe.log"


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    dump(LOG, {
        "stage": "start",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cwd": os.getcwd(),
        "exe": sys.executable,
        "argv": sys.argv,
        "env_pi": {k: v for k, v in os.environ.items() if k.startswith("PI_")},
        "env_all": dict(os.environ),
    })
    info = {}
    try:
        import maa  # noqa: F401
        from maa.agent_server import AgentServer
        info = {
            "maa_import": True,
            "maa_file": getattr(maa, "__file__", None),
            "agent_server": str(AgentServer),
            "has_start_up": hasattr(AgentServer, "start_up"),
            "has_register_custom_action": hasattr(AgentServer, "register_custom_action"),
        }
        # identifier 的候选来源：argv 第 1 个参数 / 环境变量
        cand = None
        for a in sys.argv[1:]:
            if not a.startswith("-"):
                cand = a
                break
        cand = cand or os.environ.get("MAA_AGENT_IDENTIFIER") or os.environ.get("PI_AGENT_IDENTIFIER")
        info["identifier_candidate"] = cand
        if cand:
            info["start_up"] = bool(AgentServer.start_up(cand))
    except Exception as e:  # noqa: BLE001
        info = {"maa_import": False, "err": repr(e)}
    dump(ROOT / "debug" / "agent_probe2.log", info)
    time.sleep(4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
