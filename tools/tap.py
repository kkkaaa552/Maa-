"""极简 adb tap / swipe 封装，用于助手侧人工取证。

用法：
    python tools/tap.py 927 528            # 点一下
    python tools/tap.py 927 528 --shot a   # 点完存 debug/shots/a.png
    python tools/tap.py --swipe x1 y1 x2 y2 ms
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
ADDR = "127.0.0.1:16384"


def sh(*args: str) -> str:
    r = subprocess.run([ADB, "-s", ADDR, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=20)
    return (r.stdout or "") + (r.stderr or "")


def shot(name: str) -> Path:
    out = ROOT / "debug" / "shots" / f"{name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        subprocess.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
                       stdout=f, timeout=30)
    print(f"shot -> {out}")
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--shot":          # 只截图
        time.sleep(float(argv[2]) if len(argv) > 2 else 0)
        shot(argv[1])
        return 0
    if argv[0] == "--swipe":
        x1, y1, x2, y2, ms = argv[1:6]
        print(sh("shell", "input", "swipe", x1, y1, x2, y2, ms).strip())
        return 0
    x, y = argv[0], argv[1]
    print(sh("shell", "input", "tap", x, y).strip() or f"tap {x},{y}")
    if "--shot" in argv:
        time.sleep(float(argv[argv.index("--shot") + 2]) if len(argv) > argv.index("--shot") + 2 else 1.2)
        shot(argv[argv.index("--shot") + 1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
