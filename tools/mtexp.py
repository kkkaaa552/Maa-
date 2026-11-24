"""迷踪盘实验台：可靠地进棋盘、做健全性检查、按需跑重复点击/轴可用性实验。

用法：
    python tools/mtexp.py nav            只走到棋盘并打印盘面
    python tools/mtexp.py scan           逐轴测「点下去是否改变盘面」（每轴前悔棋）
    python tools/mtexp.py gap 2 4 8      测同一轴重复点击在不同间隔下是否生效
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

import mtboard as M  # noqa: E402

ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
ADDR = "127.0.0.1:16384"
SHOTS = ROOT / "debug" / "shots"

# 入口路由（实测坐标，见 debug 记录）
NAV = [
    ((1166, 452), 2.2),   # 大厅 → 派遣公司
    ((1045, 475), 2.2),   # 派遣公司 → 茶室
    ((487, 258), 2.2),    # 茶室 → 迷踪盘关卡选择
    ((231, 270), 1.4),    # 选体验关卡卡片
    ((946, 531), 2.8),    # 前往 → 棋盘
]
BACK = (60, 35)
UNDO = (500, 592)


def to_hall() -> bool:
    """用已验收的 ReturnHall 节点回到大厅（OCR「任务」逐级点返回）。"""
    r = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "main.py", "run", "ReturnHall"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    tail = (r.stdout or "").strip().splitlines()[-8:]
    print("[ReturnHall]", " | ".join(tail))
    return "succeeded=True" in (r.stdout or "")


def cap() -> np.ndarray:
    SHOTS.mkdir(parents=True, exist_ok=True)
    raw = subprocess.run(
        [ADB, "-s", ADDR, "exec-out", "screencap", "-p"], capture_output=True, check=True
    ).stdout
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("截图解码失败")
    return img


def tap(x: int, y: int, wait: float = 1.0) -> None:
    subprocess.run([ADB, "-s", ADDR, "shell", "input", "tap", str(x), str(y)], check=True)
    time.sleep(wait)


def save(img: np.ndarray, name: str) -> Path:
    SHOTS.mkdir(parents=True, exist_ok=True)
    out = SHOTS / f"{name}.png"
    cv2.imencode(".png", img)[1].tofile(str(out))
    return out


def pieces() -> frozenset:
    return M.read_board(cap())["pieces"]


def on_board() -> bool:
    """健全性检查：3~5 枚棋子、目标点 1~5 个。偏离即认为不在棋盘。"""
    b = M.read_board(cap())
    return 1 <= len(b["pieces"]) <= 5 and 1 <= len(b["targets"]) <= 5


def to_board(tries: int = 2) -> bool:
    if on_board():
        return True
    to_hall()
    if on_board():
        return False  # 大厅不该被判成棋盘
    for i in range(tries):
        for (x, y), w in NAV:
            tap(x, y, w)
        if on_board():
            return True
        print(f"  nav 第 {i + 1} 轮后仍不在棋盘，图={save(cap(), f'navfail{i}')}")
        to_hall()
    return False


def cmd_nav() -> int:
    ok = to_board()
    b = M.read_board(cap())
    print("on_board:", ok)
    print("pieces:", sorted(b["pieces"]))
    print("targets:", sorted(b["targets"]))
    print("hexes:", sorted(b["hexes"]))
    print("save:", save(cap(), "exp_nav"))
    return 0 if ok else 1


def cmd_scan() -> int:
    if not to_board():
        print("✗ 进不去棋盘")
        return 1
    tap(*UNDO, 1.3)
    base = pieces()
    print("base:", sorted(base))
    usable, dead = [], []
    for c, (x, y) in sorted(M.CELLS.items()):
        tap(x, y, 1.0)
        p = pieces()
        (usable if p != base else dead).append(c)
        print(("OK   " if p != base else "dead "), c, sorted(p) if p != base else "")
        tap(*UNDO, 1.2)
    print("可用轴:", usable)
    print("无效轴:", dead)
    return 0


def pick_axis(state):
    """挑一个内圈轴，其环上有至少一枚棋子（点下去必然改变盘面）。"""
    for ax in sorted(M.inner_axes()):
        if M.rotate(state, ax, True) != state:
            return ax
    return None


def cmd_gap(*gaps: float) -> int:
    if not on_board():
        print("✗ 不在棋盘")
        return 1
    for gap in gaps:
        for attempt in range(3):
            tap(*UNDO, 1.4)
            base = pieces()
            ax = pick_axis(base)
            if ax is None:
                print(f"gap={gap} 当前盘面无可用轴，跳过")
                break
            X = M.CELLS[ax]
            pred1 = M.rotate(base, ax, True)
            tap(*X, 0.9)
            p1 = pieces()
            if p1 != pred1:
                print(f"gap={gap} 首次点击未按预测生效（重试 {attempt + 1}）: p1={sorted(p1)}")
                continue
            # 第二次：若该轴环上仍有棋子，则再点应继续生效
            pred2 = M.rotate(p1, ax, True)
            ring_still = pred2 != p1
            time.sleep(gap)
            tap(*X, 0.9)
            p2 = pieces()
            if not ring_still:
                print(f"gap={gap}s 轴 {ax} 首次生效，但二次前环上已无棋子（无法判定）")
                break
            print(
                f"gap={gap}s 轴 {ax}  1st→{sorted(p1)}(符合预测)  "
                f"2nd→{sorted(p2)}  符合预测={p2 == pred2}  "
                f"second_effective={p2 != p1}"
            )
            break
    return 0


def cmd_ringscan() -> int:
    """逐内圈轴实验：悔棋 → 读盘 → 点轴 → 读盘 → 与 rotate() 预测对比。

    关键在四组合判定：
      环上有棋子 & 盘面变了   → 轴可用，且搬运规则与该轴一致
      环上有棋子 & 盘面没变   → 轴不可用 / 规则不符（重点找这种）
      环上没棋子 & 盘面没变   → 无法判定（该轴对这一局面无信息）
      环上没棋子 & 盘面变了   → 规则完全错（重点找这种）
    """
    if not on_board():
        print("✗ 不在棋盘")
        return 1
    axes = sorted(M.inner_axes())
    print("内圈轴:", axes)
    tap(*UNDO, 1.4)
    base0 = M.read_board(cap())["pieces"]
    print("round base:", sorted(base0))
    rows = []
    for ax in axes:
        tap(*UNDO, 1.4)
        b0 = M.read_board(cap())
        base = b0["pieces"]
        if base != base0:
            print(f"! 悔棋后盘面不是初始态，base={sorted(base)}（记一笔）")
        pred = M.rotate(base, ax, True)
        ring_has_piece = pred != base
        tap(*M.CELLS[ax], 1.2)
        p1 = M.read_board(cap())["pieces"]
        changed = p1 != base
        if ring_has_piece and changed:
            verdict = "OK-match" if p1 == pred else "OK-but-rule-differs"
        elif ring_has_piece and not changed:
            verdict = "DEAD-AXIS"
        elif changed:
            verdict = "RULE-BROKEN"
        else:
            verdict = "no-info"
        rows.append((ax, ring_has_piece, changed, verdict, sorted(base), sorted(pred), sorted(p1)))
        print(f"\n轴 {ax} @{M.CELLS[ax]}  环上有子={ring_has_piece} 实际变化={changed}  {verdict}")
        print(f"  base={sorted(base)}")
        print(f"  pred={sorted(pred)}")
        print(f"  real={sorted(p1)}")
        if ring_has_piece and changed and p1 != pred:
            print(f"  预测有而实际无: {sorted(pred - p1)}   实际有而预测无: {sorted(p1 - pred)}")
    print("\n=== 汇总 ===")
    for ax, h, c, v, *_ in rows:
        print(f"{ax}\t环上有子={h}\t有变化={c}\t{v}")
    dead = [r[0] for r in rows if r[3] == "DEAD-AXIS"]
    print("DEAD-AXIS:", dead)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, rest = argv[1], argv[2:]
    if cmd == "nav":
        return cmd_nav()
    if cmd == "scan":
        return cmd_scan()
    if cmd == "ring":
        return cmd_ringscan()
    if cmd == "gap":
        return cmd_gap(*[float(v) for v in rest] or [2.0, 4.0, 8.0])
    print(f"未知子命令 {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
