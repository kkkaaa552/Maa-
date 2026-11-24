"""迷踪盘单步探针：按脚本依次 tap / read，每步打印盘面读数。

用法：
    python tools/mtprobe.py read tap=838,300 read tap=838,300 read

参数项：
    read            截图并打印 pieces / targets / hexes
    tap=X,Y         点一下（默认等 0.75s）
    tap=X,Y,1.2     点一下并指定等待秒数
    shot=NAME       存一张图到 debug/shots/NAME.png
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import mtboard  # noqa: E402

ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
ADDR = "127.0.0.1:16384"
SHOTS = ROOT / "debug" / "shots"


def screencap() -> np.ndarray:
    SHOTS.mkdir(parents=True, exist_ok=True)
    raw = subprocess.run(
        [ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
        capture_output=True,
        check=True,
    ).stdout
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("截图解码失败")
    return img


def read(tag: str = "") -> dict:
    img = screencap()
    b = mtboard.read_board(img)
    print(
        f"[read {tag}] pieces={sorted(b['pieces'])} targets={sorted(b['targets'])} "
        f"hexes={sorted(b['hexes'])}"
    )
    sys.stdout.flush()
    return b


def tap(x: int, y: int, wait: float = 0.75) -> None:
    subprocess.run([ADB, "-s", ADDR, "shell", "input", "tap", str(x), str(y)], check=True)
    time.sleep(wait)
    print(f"[tap] {x},{y} wait={wait}")


def main(argv: list[str]) -> int:
    for step in argv[1:]:
        if step == "read":
            read()
        elif step.startswith("tap="):
            parts = step[4:].split(",")
            tap(int(parts[0]), int(parts[1]), float(parts[2]) if len(parts) > 2 else 0.75)
        elif step.startswith("shot="):
            name = step[5:]
            SHOTS.mkdir(parents=True, exist_ok=True)
            out = SHOTS / f"{name}.png"
            cv2.imencode(".png", screencap())[1].tofile(str(out))
            print(f"[shot] {out}")
        else:
            print(f"未知项: {step}")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
