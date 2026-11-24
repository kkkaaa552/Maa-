"""连拍：点一下后按给定延时序列连续截图，用于观察动画（金环/高亮/落子）。

用法：python tools/burst.py X Y 0.1 0.3 0.6 1.0 2.0 --prefix t1
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
ADDR = "127.0.0.1:16384"


def adb(*args: str) -> None:
    subprocess.run([ADB, "-s", ADDR, *args], capture_output=True, timeout=30)


def shot(name: str) -> Path:
    out = ROOT / "debug" / "shots" / f"{name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        subprocess.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"], stdout=f, timeout=30)
    return out


def main(argv: list[str]) -> int:
    prefix = "burst"
    delays: list[float] = []
    x = y = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--prefix":
            prefix = argv[i + 1]
            i += 2
            continue
        if x is None:
            x, y = a, argv[i + 1]
            i += 2
            continue
        delays.append(float(a))
        i += 1
    if x is not None:
        adb("shell", "input", "tap", str(x), str(y))
        print(f"tap {x},{y}")
    t0 = time.time()
    for d in delays:
        while time.time() - t0 < d:
            time.sleep(0.01)
        p = shot(f"{prefix}-{d:.2f}")
        print(f"{d:.2f}s -> {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
