# -*- coding: utf-8 -*-
"""迷踪盘 Custom action：读盘 → BFS 求解 → 逐点 → 复核（闭环）。

被 pipeline 节点 `迷踪盘.求解` 调用，动作名 `迷踪盘求解`。

设计要点（每条都是实测换来的）：
1. **闭合回路而不是开环播放**。每点一下都重新截图读数，跟预测比对；
   只要不一致就按实测状态重规划。所以旋转模型即使有边界情况没摸清，
   也不会把盘面带进死局。
2. **目标点开局投票锁定，中途不再改**。左侧小盘绿点单帧会偶发漏读
   （实测一局里 4 个掉成 3 个），求解器按残缺集合算会直接判「无解」并
   放弃整局。开局连拍 5 帧逐格投票（过半采信），此后单帧变化只记日志。
3. **六边形格可以当轴点**，棋子不会落入其中（实测点 (-1,1) 后
   环上 (0,1)→(-1,2)、(-2,1)→(-1,0)，六边形自身不动）。求解时仍把它
   当作「棋子不得落入」的保守约束，命中不了再放开。
4. **没有「一个轴只能用一次」的限制，也没有冷却**（实测 0.75s / 2s / 5s
   间隔重复点击同一轴全部生效，且每步都符合 rotate() 预测）。轴能不能点
   只取决于几何：1 环 6 邻格必须全在盘内，即 inner_axes() 那 7 个。盘边
   的轴点没反应不是被消耗，而是环上缺格。
5. **旋转方向是盘面的全局状态**。右下 (1170,590) 的「顺/逆」按钮切换整个
   棋盘的旋转方向，切完一直保持。实测某一局只按顺时针求解时 7 个内圈轴
   全部无解，点一下按钮变逆时针，6 步就通。所以求解必须用 solve_bi()，
   把方向当成状态维度；本函数还维护 `board_cw` 这一信念，第一次点击与预测
   不符时判定为「方向信念错了」，翻一次按钮后重规划。
6. **紧跟在切方向之后的点击会被吞**。实测某局日志里「切方向」与紧接着的一步都没
   生效，盘面停在两步之前的状态；手工补点一下就通关。所以切方向后等 DIR_WAIT
   (1.8s)，且每次点击都复核，盘面没动就拉长等待重试一次。旧版还会在
   「收官手」分支直接返回成功不做验证（理由是「完成遮罩会盖住盘面读不到」），
   结果把「其实没通关」误报成成功，交给 pipeline 的模板去兜，必是 20s 后超时。
   现在一律按实测棋子集是否等于目标集判成败。
7. **连续 3 次同一首步与预测不符（且重试也没让盘面动）才判失败**——说明该轴
   与旋转模型不符。没有「解除保守约束」那套：blocked 只能按六边形读数更新。

入口自检（无 Client）：
    .venv\\Scripts\\python.exe agent\\mizongpan.py --selftest
读一张离线图算解法（不点）：
    .venv\\Scripts\\python.exe agent\\mizongpan.py debug/shots/a2.png
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "debug" / "mt_agent.log"
sys.path.insert(0, str(ROOT / "tools"))

MAX_STEPS = 24
TAP_WAIT = 0.75          # 单次点击后等动画
TAP_RETRY_WAIT = 1.4     # 点击被判为「被吞」后重试前的等待
DIR_WAIT = 1.8           # 切方向后的等待：切换带过渡动画，比单次点击更长
BOARD_WAIT = 12.0        # 等棋盘出现（目标点读出来）的最长秒数
POLL = 0.35
TARGET_VOTES = 5         # 目标点多帧投票的帧数


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def sortc(s):
    return sorted(s, key=lambda c: (c[1], c[0]))


def vote_targets(grab, cells, votes=TARGET_VOTES, gap=0.12) -> frozenset:
    """多帧投票取目标点：逐格统计出现次数，过半即采信。

    为什么必须投票：左侧小盘的绿点读数是 19 格逐格判定的，单帧会偶发漏读
    某个绿点（实测一局里 4 个掉成 3 个）。求解器按残缺目标集算，会直接判
    「无解」并放弃整局。逐格投票对「偶发漏读」和「偶发误报」都免疫。
    """
    count = {c: 0 for c in cells}
    ok = 0
    for _ in range(votes):
        try:
            b = grab()
        except Exception:
            continue
        ok += 1
        for c in b["targets"]:
            if c in count:
                count[c] += 1
        time.sleep(gap)
    if ok == 0:
        return frozenset()
    need = ok // 2 + 1
    return frozenset(c for c, n in count.items() if n >= need)


def calibrate_dir(grab, tap_cell, P, blocked):
    """点一个「环上有子」的轴，用实测结果判定当前是顺时针还是逆时针。

    方向是盘面全局状态，开局未知。挑一个顺/逆预测都非空且互不相同的轴点一次，
    对照两种预测即可定死方向。判不出来就返回 None（调用方退回保守信念）。
    """
    from mtboard import inner_axes, rotate

    for ax in sorted(inner_axes()):
        p_cw = rotate(P, ax, cw=True, blocked=blocked)
        p_ccw = rotate(P, ax, cw=False, blocked=blocked)
        if p_cw == P or p_ccw == P or p_cw == p_ccw:
            continue
        tap_cell(ax)
        time.sleep(TAP_WAIT)
        got = grab()["pieces"]
        if not got:
            continue
        if got == p_cw:
            log(f"方向校准：点 {ax} 得 {sortc(got)} → 当前为顺时针")
            return True, got
        if got == p_ccw:
            log(f"方向校准：点 {ax} 得 {sortc(got)} → 当前为逆时针")
            return False, got
        log(f"方向校准：点 {ax} 实测 {sortc(got)} 与顺/逆预测都不符，换轴再试")
        return None, got
    return None, P


def solve_once(ctl, max_steps=MAX_STEPS) -> tuple[bool, str]:
    """在棋盘页闭环求解。ctl 是 controller（有 post_screencap/post_click）。"""
    from mtboard import CELLS, SMALL, read_board, rotate, solve_bi

    DIR_BTN = (1170, 590)

    def grab():
        img = ctl.post_screencap().wait().get()
        if img is None:
            raise RuntimeError("截图返回空")
        return read_board(img)

    def tap_at(x, y):
        ctl.post_click(int(x), int(y)).wait()

    def tap_cell(c):
        x, y = CELLS[c]
        tap_at(x, y)

    # 1) 等棋盘能读到内容
    t0 = time.time()
    b = None
    while time.time() - t0 < BOARD_WAIT:
        try:
            b = grab()
        except Exception:
            b = None
        if b and (b["targets"] or b["pieces"]):
            break
        time.sleep(POLL)
    if not b:
        return False, "等不到棋盘（截图一直读不到盘面）"

    # 2) 目标点投票锁定：开局定死，中途绝不因单帧读数改动
    T = vote_targets(grab, SMALL)
    if not T:
        return False, "左侧小盘读不到任何绿点（无法确定解密点）"
    P, H = b["pieces"], b["hexes"]
    log(f"开局 棋子 {sortc(P)} 目标 {sortc(T)}（{TARGET_VOTES} 帧投票）六边形 {sortc(H)}")
    if not P:
        time.sleep(0.6)
        b = grab()
        P, H = b["pieces"], b["hexes"]
        log(f"复读 棋子 {sortc(P)} 六边形 {sortc(H)}")
    if not P:
        return False, "开局读不到任何棋子"

    blocked = set(H)

    # 3) 方向校准（方向未知就点一次试出来；试不出来就退回顺时针信念，
    #    后面每一步点击不符时会自动翻面纠正）
    board_cw = True
    cw, P_cal = calibrate_dir(grab, tap_cell, P, frozenset(blocked))
    if cw is None:
        if P_cal and P_cal != P:
            P = P_cal  # 盘面确实动了，按实测继续
        log("方向未定，先按顺时针假定，点击不符时自动翻面")
    else:
        board_cw, P = cw, P_cal

    miss = {}
    for step in range(max_steps):
        if sorted(P) == sorted(T):
            log(f"已达成（{step} 步）：{sortc(P)}")
            return True, f"{step} 步达成"

        plan = solve_bi(P, T, frozenset(blocked), cw=board_cw)
        if plan is None:
            # 方向信念可能反了：翻面重算
            plan2 = solve_bi(P, T, frozenset(blocked), cw=not board_cw)
            if plan2 is not None:
                log(f"当前信念({'顺' if board_cw else '逆'})无解，翻面后 {len(plan2)} 步可行")
                plan = plan2
                tap_at(*DIR_BTN)
                time.sleep(DIR_WAIT)
                board_cw = not board_cw
            else:
                # 双向都无解：只剩读书可能错（目标集或六边形集合）
                T2 = vote_targets(grab, SMALL)
                if T2 and T2 != T:
                    log(f"无解复核：目标重投得 {sortc(T2)}（原 {sortc(T)}），改按新集合继续")
                    T = T2
                    continue
                b = grab()
                H2 = b.get("hexes", frozenset())
                if H2 and set(H2) != blocked:
                    log(f"无解复核：六边形读数为 {sortc(H2)}（原 {sortc(blocked)}），改按新集合继续")
                    blocked = set(H2)
                    continue
                return False, (f"双向均无解：棋子 {sortc(P)} 目标 {sortc(T)} "
                               f"六边形 {sortc(blocked)} 方向{'顺' if board_cw else '逆'}")

        kind, ax = plan[0]
        if kind == "flip":
            log(f"[{step}] 切方向 → {'逆' if board_cw else '顺'}")
            tap_at(*DIR_BTN)
            time.sleep(DIR_WAIT)   # 切换有过渡动画，等太短会吞掉紧跟的那一下
            board_cw = not board_cw
            continue

        pred = rotate(P, ax, cw=board_cw, blocked=frozenset(blocked))
        if pred == P:
            # 该轴本次无效（环上子都要进六边形 / 目标被占），不该被选为动作
            log(f"[{step}] 轴 {ax} 预测为空转，跳过并记入 miss")
            miss[ax] = miss.get(ax, 0) + 1
            if miss[ax] >= 3:
                return False, f"轴 {ax} 连续 3 次空转，模型与实机不符"
            continue

        log(f"[{step}] 余 {len(plan)} 步 首步 {ax}"
            f"（{'顺' if board_cw else '逆'}）预测 {sortc(pred)}")

        # 点一下并复核；盘面没动 = 这一下被吞了（紧跟切方向 / 动画未结束），
        # 拉长等待再补一次。不重试的话求解器会拿旧状态继续规划，越走越远。
        P2 = P
        for attempt in (0, 1):
            tap_cell(ax)
            time.sleep(TAP_WAIT if attempt == 0 else TAP_RETRY_WAIT)
            b = grab()
            P2 = b["pieces"]
            if P2 == pred or P2 == P:
                break
        T2 = b["targets"]

        if not P2:
            # 盘面读不到棋子：多半是这一手通关、棋盘被「完成」遮罩盖住了。
            # 读空不能直接当成功（旧版就是在这里无条件 return True，把没通关
            # 误报成成功），只有「预测本来就等于目标集」才认。
            time.sleep(0.6)
            b = grab()
            P2 = b["pieces"]
            T2 = b["targets"]
            if not P2:
                if sorted(pred) == sorted(T):
                    log(f"    盘面被完成遮罩盖住，且这一手预测即目标集 → 判定通关")
                    return True, f"{step + 1} 步达成"
                return False, f"第 {step} 步后盘面读不到棋子（预测 {sortc(pred)}）"

        if sorted(P2) == sorted(T):
            log(f"    达成目标：{sortc(P2)}（{step + 1} 步）")
            return True, f"{step + 1} 步达成"
        if T2 and T2 != T:
            log(f"    目标单帧读数 {sortc(T2)} 与锁定集不符，按噪声忽略")

        if P2 != pred:
            # 先怀疑方向信念反了：用另一方向重算同一轴的预测
            alt = rotate(P, ax, cw=not board_cw, blocked=frozenset(blocked))
            if alt != pred and P2 == alt:
                log(f"    实测符合反向预测 → 方向信念翻面为 {'逆' if board_cw else '顺'}")
                board_cw = not board_cw
            else:
                log(f"    实测 {sortc(P2)} 与预测不符")
                if P2 == P:
                    log("    盘面未动（重试仍被吞）：该轴本次空转")
                    miss[ax] = miss.get(ax, 0) + 1
                    if miss[ax] >= 3:
                        return False, f"轴 {ax} 连续 3 次空转（点击未生效），模型与实机不符"
                else:
                    miss[ax] = 0
                H2 = b.get("hexes", frozenset())
                if H2 and set(H2) != blocked:
                    log(f"    六边形读数 {sortc(H2)} 与预测时不同（原 {sortc(blocked)}），按新读数重算")
                    blocked = set(H2)
        P = P2
    return (sorted(P) == sorted(T)), f"达到步数上限 {max_steps}"


def register() -> None:
    from maa.agent.agent_server import AgentServer
    from maa.custom_action import CustomAction

    @AgentServer.custom_action("迷踪盘求解")
    class SolveMizongpan(CustomAction):
        def run(self, context, argv) -> bool:
            try:
                ok, msg = solve_once(context.tasker.controller)
            except Exception:
                msg = "迷踪盘求解异常：\n" + traceback.format_exc()
                log(msg)
                ok = False
            log(f"迷踪盘求解 {'成功' if ok else '失败'}：{msg}")
            return CustomAction.RunResult(success=bool(ok))


def selftest(path: str | None = None) -> int:
    import cv2
    import numpy as np
    from mtboard import read_board, solve_bi

    p = Path(path) if path else ROOT / "debug" / "shots" / "a2.png"
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR) \
        if p.exists() else None
    if img is None:
        log(f"读不到 {p}")
        return 1
    b = read_board(img)
    P, T, H = b["pieces"], b["targets"], b["hexes"]
    log(f"{p.name} 棋子 {sortc(P)} 目标 {sortc(T)} 六边形 {sortc(H)}")
    for cw in (True, False):
        plan = solve_bi(P, T, H, cw=cw)
        log(f"起手{'顺' if cw else '逆'} 解法 {plan}")
    return 0


def main(argv: list[str]) -> int:
    log("=" * 60)
    if "--selftest" in argv:
        return selftest()
    png = [a for a in argv[1:] if a.lower().endswith(".png")]
    if png:
        return selftest(png[0])
    from maa.agent.agent_server import AgentServer
    identifier = argv[1] if len(argv) > 1 else "mizongpan"
    register()
    log(f"agent 启动（mizongpan），identifier = {identifier}")
    AgentServer.start_up(identifier)
    AgentServer.join()
    AgentServer.shut_down()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
