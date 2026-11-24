"""仓库数量识别：字形模板 + 最近邻判别（含小数点与「万」）。

建模页：物料 / 资源（已标注）；验证页：票券。
用法:
    python tools/wh_counts.py            # 建立模板并交叉验证
    python tools/wh_counts.py --dump     # 额外打印每格判别明细
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_digits import SHOT_DIR, read_gray  # noqa: E402

X0, Y0, PX, PY = 365, 130, 150, 128
DX0, DX1 = 24, 108          # 数量窗口：右对齐到 +105，右端留 3px 防纸边；
                            # 左边从 38 放宽到 24 —— 实跑出现「121.38万」(7 个字形、约 75px)，
                            # 70px 窗口会把前两位整段截掉，读成「1.38万」。
DY0, DY1 = 88, 108
# 2026-11-24 14:53 由 165 降到 155。
# 原因：滚到页面最后一屏时那一帧整体偏暗（数量带平均灰度 ~61，中间屏是 ~75），数字的抗锯齿
# 边缘被打散成孤立碎片 —— 实测「120」的「1」在 165 下只剩 4x13 但有效像素 17 个，比 area>=18
# 少 1 个就被当噪声丢掉，于是「12」读成「2」、「43」读成「13」、「30」读成「3」。
# 实测：三张样本图 LABELS 在 155 下仍 62/62 全吻合（训练集不受影响）；
#       全部 7 屏实跑读数只有 8 格变化，且全部是由错变对（物料第4行 00/1 → 100/40，
#       资源第4行 0/13/1/3/8/空 → 120/43/4/30/18/5），其余 152 格一字未动。
THR = 155

# 2026-11-24 13:56 重截重标（上一批 11/19 的图在 11/23 22:00 清目录时丢失）。
# 读数由 6× 放大后的数量窗口逐格目视确认，且与 glyphs() 切字数逐格一致（见 _report.txt）。
LABELS = {
    "wh-06-material-stable-135653.png": {
        (0, 0): "1", (0, 1): "13", (0, 2): "1", (0, 3): "1", (0, 4): "12", (0, 5): "75",
        (1, 0): "20", (1, 1): "10", (1, 2): "92", (1, 3): "8", (1, 4): "57", (1, 5): "1",
        (2, 0): "1", (2, 1): "1", (2, 2): "2", (2, 3): "3", (2, 4): "104", (2, 5): "171",
        (3, 0): "25", (3, 1): "1", (3, 2): "19", (3, 3): "64", (3, 4): "45", (3, 5): "49",
    },
    "wh-07-resource-stable-135723.png": {
        (0, 0): "15", (0, 1): "27", (0, 2): "2", (0, 3): "4", (0, 4): "2", (0, 5): "18",
        (1, 0): "49", (1, 1): "5", (1, 2): "7", (1, 3): "10", (1, 4): "2", (1, 5): "1",
        (2, 0): "1", (2, 1): "1", (2, 2): "23", (2, 3): "2", (2, 4): "5", (2, 5): "38",
        (3, 0): "130", (3, 1): "29", (3, 2): "4", (3, 3): "365", (3, 4): "1", (3, 5): "2",
    },
    "wh-09-ticket-stable-135739.png": {
        (0, 0): "40", (0, 1): "1.78万", (0, 2): "21", (0, 3): "1530", (0, 4): "1", (0, 5): "80",
        (1, 0): "2427", (1, 1): "13", (1, 2): "80", (1, 3): "400", (1, 4): "20", (1, 5): "2250",
        (2, 0): "236.66万", (2, 1): "2000",
    },
}

TRAIN = ["wh-06-material-stable-135653.png", "wh-07-resource-stable-135723.png"]
TEST = ["wh-09-ticket-stable-135739.png"]


def glyphs(shot: np.ndarray, r: int, c: int) -> list[tuple[int, np.ndarray]]:
    x, y = X0 + c * PX, Y0 + r * PY
    reg = shot[y + DY0:y + DY1, x + DX0:x + DX1]
    mask = (reg > THR).astype(np.uint8) * 255
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        gx, gy, gw, gh, area = (int(v) for v in stats[i])
        if gy == 0:                      # 顶部边框
            continue
        if gx + gw >= reg.shape[1] - 1:  # 贴到窗口右缘 = 纸边
            continue
        is_digit = 12 <= gh <= 17 and 2 <= gw <= 12 and area >= 18
        is_wan = 12 <= gh <= 18 and 13 <= gw <= 22 and area >= 40
        is_dot = 1 <= gh <= 6 and 1 <= gw <= 6 and area >= 2 and gy >= 14
        if not (is_digit or is_wan):
            continue
        g = reg[gy:gy + gh, gx:gx + gw]
        out.append((gx, g))
    # 小数点用低阈值单独找：白色很弱，165 会漏
    mask2 = (reg > 120).astype(np.uint8) * 255
    n2, _l2, st2, _c2 = cv2.connectedComponentsWithStats(mask2, 8)
    for i in range(1, n2):
        gx, gy, gw, gh, area = (int(v) for v in st2[i])
        if not (1 <= gh <= 6 and 1 <= gw <= 6 and area >= 2 and gy >= 14):
            continue
        if gx + gw >= reg.shape[1] - 1:
            continue
        out.append((gx - 0.5, reg[gy:gy + gh, gx:gx + gw]))
    out.sort(key=lambda t: t[0])
    return out


def prep(g: np.ndarray, H: int = 28) -> np.ndarray:
    gh, gw = g.shape
    s = H / gh
    nw = max(1, min(26, int(round(gw * s))))
    return cv2.resize(g.astype(np.float32), (nw, H), interpolation=cv2.INTER_CUBIC)


def sim(a: np.ndarray, b: np.ndarray) -> float:
    H = min(a.shape[0], b.shape[0])
    W = min(a.shape[1], b.shape[1])
    A = cv2.resize(a, (W, H))
    B = cv2.resize(b, (W, H))
    best = -2.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            c = float(cv2.matchTemplate(A, cv2.warpAffine(B, M, (W, H), borderValue=float(B.mean())),
                                        cv2.TM_CCOEFF_NORMED)[0, 0])
            best = max(best, c)
    return best


DIGITS_DIR = ROOT / "data" / "digits"


def build_from_digits() -> dict[str, list[np.ndarray]]:
    """从 data/digits/d0.png … d9.png 读固化的中位字模。

    这是 build() 的兜底路径：训练截图放在 debug/shots/，而 debug/ 既不进发布包也不进
    git（.gitignore），最终用户拿到 zip 后根本没有那三张图。data/digits/ 是 build_digits.py
    从同一批标注里蒸馏出来的 28×20 中位模板，大小固定、随包发布，用它建 bank 语义等价。

    返回结构与 build() 一致：{字符: [模板数组, ...]}；缺任何一张就返回空 dict 让调用方决定。
    """
    bank: dict[str, list[np.ndarray]] = {}
    for ch in "0123456789":
        p = DIGITS_DIR / f"d{ch}.png"
        if not p.is_file():
            print(f"[字模] 缺 {p}，无法从 data/digits 建 bank")
            return {}
        img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            print(f"[字模] 读不出 {p}")
            return {}
        bank[ch] = [prep(img)]
    return bank


def build(files: list[str]) -> dict[str, list[np.ndarray]]:
    """优先用训练截图建 bank；截图缺失时退回 data/digits 固化字模。

    坑（2026-11-24 实测）：早先这里对缺失截图是裸崩 ——
    `read_gray(SHOT_DIR / name)` 直接抛 FileNotFoundError，整个 scan_image 挂掉，
    任务以 ret=false 结束（就是最初「仓库识别任务识别失败」的根因）。
    发布包里 debug/ 不存在，所以必须兜底。
    """
    if not all((SHOT_DIR / name).is_file() for name in files):
        missing = [n for n in files if not (SHOT_DIR / n).is_file()]
        print(f"[字模] 训练截图缺失 {len(missing)} 张（{missing[0]} …），改用 data/digits 固化字模")
        bank = build_from_digits()
        if bank:
            return bank
        raise FileNotFoundError(
            f"训练截图与 data/digits 都不可用；缺截图: {missing}；缺字模目录: {DIGITS_DIR}"
        )

    bank: dict[str, list[np.ndarray]] = {}
    for name in files:
        shot = read_gray(SHOT_DIR / name)
        for (r, c), lab in LABELS[name].items():
            gs = glyphs(shot, r, c)
            if len(gs) != len(lab):
                print(f"[跳过] {name} {(r,c)} 期望{lab} 切出{len(gs)}")
                continue
            for ch, (_, g) in zip(lab, gs):
                bank.setdefault(ch, []).append(prep(g))
    return bank


def classify(g: np.ndarray, bank: dict[str, list[np.ndarray]]) -> tuple[str, float]:
    gh, gw = g.shape
    if gh <= 6 and gw <= 6:          # 小数点
        return ".", 1.0
    if gw >= 13:                     # 「万」
        return "万", 1.0
    q = prep(g)
    scores = {k: max(sim(q, t) for t in v) for k, v in bank.items()}
    best = max(scores, key=scores.get)
    return best, scores[best]


def main() -> int:
    bank = build(TRAIN)
    print("模板字符:", " ".join(f"{k}={len(v)}" for k, v in sorted(bank.items())))
    ok = tot = 0
    for name in TEST:
        shot = read_gray(SHOT_DIR / name)
        for (r, c), lab in LABELS[name].items():
            gs = glyphs(shot, r, c)
            got = "".join(classify(g, bank)[0] for _, g in gs)
            tot += 1
            hit = got == lab
            ok += hit
            flag = "OK " if hit else "ERR"
            print(f"{flag} {name[:10]} {(r,c)} 期望={lab} 得到={got}")
    print(f"验证 {ok}/{tot} = {ok / tot:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
