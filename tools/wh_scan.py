"""仓库扫描：每格 = 物品身份（图标 NCC + HSV 直方图融合）+ 数量（字形模板）。

用法:
    python tools/wh_scan.py <截图> [--cat 物料] [--json data/warehouse/x.json] [--rows 4]

判据（2026-11-22 改：旧口径 0.5*NCC灰度+0.5*HSV直方图，同族图标 margin 只有 0.001~0.015，撞不过 0.03 的阈值）:
    score  = 0.7*(B,G,R 三通道 NCC 的均值) + 0.3*(色度通道 B-G、G-R 的 NCC 均值)
             色度项专门分「同一张画、只差颜色」的同族图标（冬谷神秘水/冬谷畅饮水、盈级装备匣四变体）
    margin = 第一名 - 第二名
    hit     : score>=0.30 且 margin>=0.03
    empty   : 读不到数量 且 margin<0.02
    其余 → uncertain（存图到 debug/shots/unknown-*.png，交人工或点格子 OCR 兜底）

模板库: resource/image/物品/*.png —— 游戏内图标（128x128 透明底，文件名即物品名），
        比 resource/image 里的图鉴插图准得多（图鉴图带描边、视角不同）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_digits import SHOT_DIR  # noqa: E402
import wh_counts  # noqa: E402

IMAGE_DIR = ROOT / "resource" / "image"
ICON_DIR = IMAGE_DIR / "物品"          # 物品图标（128x128 透明底，文件名=物品名），与 UI 模板分开
UNKNOWN_DIR = ROOT / "debug" / "shots"
WHITELIST_PATH = ROOT / "data" / "whitelist.json"   # 识别白名单（tools/build_whitelist.py 生成）

X0, Y0, PX, PY, CW, CH = 365, 130, 150, 128, 100, 100
COLS = 6
CROP = 124            # 以格子中心裁的正方形边长（比格子大，容图标溢出）
TPL_SIZE = 104        # 模板缩到该边长，在 CROP 里滑动搜索
HIT_SCORE, HIT_MARGIN = 0.30, 0.03
EMPTY_SCORE = 0.25
# 数量必须长成这样才算读到了：48 / 1.39万 / 87.49万。
# 空白纸上凑出来的 ".", "万", "..4" 这类垃圾串一律不算（见 scan_image 里的 qty_ok）。
QTY_RE = re.compile(r"\d+(?:\.\d+)?(?:万)?")


CHROMA_W = 0.3      # 色度项权重（0=纯逐通道 NCC；0.3 时物料页 21/22 名字对、margin 中位 0.125）


def _read_color(p: Path) -> np.ndarray | None:
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def hist_of(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [18, 16], [0, 180, 0, 256])
    cv2.normalize(h, h)
    return h


def chroma_of(bgr: np.ndarray) -> np.ndarray:
    """去亮度色度：B-G、G-R。同族图标（同画不同色）在这一层才分得开。"""
    b, g, r = (bgr[:, :, i].astype(np.float32) for i in range(3))
    return np.dstack([b - g, g - r])


def _ncc(img: np.ndarray, tpl: np.ndarray) -> float:
    return float(cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED).max())


NAME_HASH: dict[str, str] = {}        # 物品名 → 图标文件 md5
HASH_NAMES: dict[str, list[str]] = {}  # md5 → 用同一张图的所有名字（等级不同但图一样）
FAMILY: dict[str, list[str]] = {}      # 族名（去掉末尾 I/II/V/数字）→ 该族所有等级名

GRADE_SUFFIX = ("I", "II", "III", "IV", "V")
GRADED_FAMILY: set[str] = set()        # 名字去掉罗马数字后，属于「图标库里有 I~V 五档」的族


def load_whitelist() -> set[str] | None:
    """读 data/whitelist.json，返回允许识别的名字集合（未做 norm，调用方自己归一）。

    用户 2026-11-24 定：仓库识别**只识别**三份图鉴 xlsx 里存在的名字，名单外的一律不认。
    这样做的直接收益是「误报变成不报」——之前物料页那格「加密冬谷礼盒」的 NCC 只有 0.28，
    169 个候选里最高分才 0.28，等于随机挑一个；名单里没有真名时，NCC 再高也是猜。

    文件不存在时返回 None（退回旧的全库识别），并打一行警告：
    宁可让它跑，也不要因为少个 JSON 就整条任务崩掉。
    """
    if not WHITELIST_PATH.is_file():
        print(f"[警告] 白名单缺失: {WHITELIST_PATH}，本次退回全库识别（误报会回来）")
        return None
    try:
        data = json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))
    except Exception as e:                                  # noqa: BLE001
        print(f"[警告] 白名单解析失败: {e}，本次退回全库识别")
        return None
    names: set[str] = set()
    for cat_names in (data.get("cats") or {}).values():
        names.update(str(n).strip() for n in cat_names if str(n).strip())
    return names or None


def load_icons(whitelist: set[str] | None = None) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """读 image\\物品\\ 里的物品图标（128x128 透明底，文件名=物品名）。

    单独放子目录是因为 image 根目录还混着 pipeline 的 UI 模板（物料.png、返回.png…）
    和几张 133~256 见方的大图鉴插图，按尺寸过滤会把它们误收进来污染匹配。

    whitelist 不为空时，只收名字在名单里的模板（用户 2026-11-24 定的规矩）；
    传 None 表示调用方自己已筛过，或明确要全库识别。
    """
    if whitelist is None:
        whitelist = load_whitelist()
    out = []
    skipped: list[str] = []
    # 每次重建都要清空：这函数在一个进程里会被调用多次（每页一次、探针脚本再一次），
    # 不清空就会把同一个文件名反复 append 进 HASH_NAMES，害得所有物品都被当成
    # 「同图多等级」而收敛成族名、打上「等级待定」（2026-11-22 15:39 那一轮就是这样）。
    NAME_HASH.clear()
    HASH_NAMES.clear()
    FAMILY.clear()
    GRADED_FAMILY.clear()
    for p in sorted(ICON_DIR.glob("*.png")):
        # 白名单过滤：文件名不在名单里就跳过。例外是 ICON_ALIAS 里登记的「另一个样子」，
        # 它的名字改回白名单里的那个（冬谷币2 → 冬谷币），当作同一物品的第二张模板。
        stem = ICON_ALIAS.get(p.stem, p.stem)
        if whitelist is not None and stem not in whitelist:
            skipped.append(p.stem)
            continue
        raw = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if raw is None:
            continue
        if raw.ndim == 3 and raw.shape[2] == 4:
            a = raw[:, :, 3:4].astype(np.float32) / 255.0
            img = (raw[:, :, :3].astype(np.float32) * a + 30.0 * (1 - a)).astype(np.uint8)
        else:
            img = raw if raw.ndim == 3 else cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
        c = (cv2.resize(img, (TPL_SIZE, TPL_SIZE), interpolation=cv2.INTER_AREA)
             if min(img.shape[:2]) > TPL_SIZE else img)   # 小于 TPL_SIZE 的模板不放大（如改名卡 66x66）
        out.append((stem, c.astype(np.float32), chroma_of(c)))
        # 记下「哪几个名字其实是同一张图」：考核自选包I~V、核定牌自选包I~V 各 5 个文件
        # 逐字节相同，匹配得分也完全相同，不记下来的话 margin 永远是 0，只能判存疑。
        h = hashlib.md5(p.read_bytes()).hexdigest()
        NAME_HASH[stem] = h
        HASH_NAMES.setdefault(h, []).append(stem)
        base = re.sub(r"[IVX0-9]+$", "", stem)
        if base and base != stem:
            FAMILY.setdefault(base, []).append(stem)
    # 哪些族真的是「I~V 五档」：冬谷币/冬谷币2 也被 FAMILY 收在一起，但它不是等级族
    # （后缀 2 不在罗马数字里），按等级判据处理会把「冬谷币2」当成 II 档。这里只认
    # 后缀去掉族名后恰好落在 I..V 的族。
    for base, mem in FAMILY.items():
        suffixes = {m[len(base):] for m in mem}
        if suffixes <= set(GRADE_SUFFIX):
            GRADED_FAMILY.add(base)
    build_word_family(out)
    build_diff_family(out)
    if whitelist is not None:
        print(f"[白名单] 启用：{len(out)} 个模板参与识别"
              + (f"，名单外排除 {len(skipped)} 个: {skipped}" if skipped else ""))
    return out


_ICONS_CACHE: list[tuple[str, np.ndarray, np.ndarray]] | None = None
_BANK_CACHE: dict[str, list[np.ndarray]] | None = None
_DENY_CACHE: list[tuple[str, np.ndarray, np.ndarray]] | None = None

DENY_DIR = IMAGE_DIR / "_deny"      # 名单外物品的负样本模板（文件名 _deny-<说明>.png）
DENY_SCORE = 0.55                   # 命中负样本的分数门槛，见 load_deny() 里的实测依据

# 文件名不在白名单、但实际是白名单里某个名字的「另一样子」的模板。
# 冬谷币2 是从游戏内截图抠出来的（64x60，带格子暗底），而图鉴的 冬谷币.png 是
# 128x128 透明底图标 —— 同一枚铜钱，一个贴屏幕一个贴白纸，NCC 只有 -0.035。
# 只留图鉴那张的话，票券第三行第一个「冬谷币 237.16万」会整格认不出来。
# 这里把截图版登记成 冬谷币 的第二个模板：谁更像用谁，名字仍只报 冬谷币。
ICON_ALIAS = {"冬谷币2": "冬谷币"}


def _icons_cached() -> list[tuple[str, np.ndarray, np.ndarray]]:
    """load_icons() 的进程级缓存。

    一页要扫 24 格、一次抓取要扫 3~4 页，scan_image 每屏都重建索引会把 169 个 png
    反复解码（实测单页多花 2 秒左右），而且每次都要重打一行白名单日志。
    模板图在一次运行里不会变，缓存到进程结束即可。
    """
    global _ICONS_CACHE
    if _ICONS_CACHE is None:
        _ICONS_CACHE = load_icons()
    return _ICONS_CACHE


def _bank_cached() -> dict[str, list[np.ndarray]]:
    """数字字模 bank 的进程级缓存（同样是每屏重建一次太浪费）。"""
    global _BANK_CACHE
    if _BANK_CACHE is None:
        _BANK_CACHE = wh_counts.build(wh_counts.TRAIN)
    return _BANK_CACHE


def reset_caches() -> None:
    """丢掉图标与字模缓存。改过模板图或白名单后（同一进程内）需要手动调一次。"""
    global _ICONS_CACHE, _BANK_CACHE, _DENY_CACHE
    _ICONS_CACHE = None
    _BANK_CACHE = None
    _DENY_CACHE = None
    WORD_FAMILY.clear()
    _CHROMA_BY_NAME.clear()
    _TPL_BY_NAME.clear()
    DIFF_FAMILY.clear()
    DIFF_MASKS.clear()


def load_deny() -> list[tuple[str, np.ndarray, np.ndarray]]:
    """读 image\\_deny\\ 里的「名单外物品」负样本模板。

    用户 2026-11-24 定：仓库识别只认名单里的物品，名单外的一律不认。但白名单只解决
    「候选池里没有正确名字」，解决不了「必须挑一个最像的」——名单外的兑换券、紫信封
    照样被强配给白名单里最像的「改名卡 0.366」「家具蓝图 0.395」。

    想用 score 阈值切掉它们不行：名单内最差的真物品（轻锐核定牌V）是 0.412，只差 0.017。
    改成给这些已知的名单外物品各裁一张模板，让它们先把自己认出来（实测自匹配
    兑换券 0.694、紫信封 0.739，而格子上其它模板最高才 0.39），再整格判空。

    加新负样本：截一张该物品在仓库格里的图（100x100 左右即可），存成
    image\\_deny\\_deny-<说明>.png。文件名只作日志用，前缀 _deny- 是约定。
    """
    out = []
    if not DENY_DIR.is_dir():
        return out
    for p in sorted(DENY_DIR.glob("*.png")):
        raw = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if raw is None:
            continue
        if raw.ndim == 3 and raw.shape[2] == 4:
            a = raw[:, :, 3:4].astype(np.float32) / 255.0
            img = (raw[:, :, :3].astype(np.float32) * a + 30.0 * (1 - a)).astype(np.uint8)
        else:
            img = raw if raw.ndim == 3 else cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
        c = (cv2.resize(img, (TPL_SIZE, TPL_SIZE), interpolation=cv2.INTER_AREA)
             if min(img.shape[:2]) > TPL_SIZE else img)
        out.append((p.stem.removeprefix("_deny-"), c.astype(np.float32), chroma_of(c)))
    if out:
        print(f"[名单外] 已加载 {len(out)} 个负样本模板: {[o[0] for o in out]}")
    return out


def _deny_cached() -> list[tuple[str, np.ndarray, np.ndarray]]:
    global _DENY_CACHE
    if _DENY_CACHE is None:
        _DENY_CACHE = load_deny()
    return _DENY_CACHE


def _deny_hit(patch: np.ndarray, chroma: np.ndarray,
              deny: list[tuple[str, np.ndarray, np.ndarray]]) -> tuple[str, float]:
    """这一格是不是某个名单外物品？是就返回 (名字, 分数)，否则 ("", 0)。"""
    best_name, best = "", 0.0
    for name, tb, tc in deny:
        n1 = (_ncc(patch[:, :, 0], tb[:, :, 0]) + _ncc(patch[:, :, 1], tb[:, :, 1])
              + _ncc(patch[:, :, 2], tb[:, :, 2])) / 3.0
        n2 = (_ncc(chroma[:, :, 0], tc[:, :, 0]) + _ncc(chroma[:, :, 1], tc[:, :, 1])) / 2.0
        s = (1 - CHROMA_W) * n1 + CHROMA_W * n2
        if s > best:
            best_name, best = name, s
    return (best_name, best) if best >= DENY_SCORE else ("", 0.0)


def cell_patch(shot: np.ndarray, r: int, c: int) -> tuple[np.ndarray, np.ndarray]:
    """以格子中心裁 CROP 见方（图标常溢出格子，所以比格子大），交给 matchTemplate 滑动搜索。"""
    cx, cy = X0 + c * PX + CW // 2, Y0 + r * PY + CH // 2
    x, y = cx - CROP // 2, cy - CROP // 2
    h, w = shot.shape[:2]
    x = max(0, min(x, w - CROP))
    y = max(0, min(y, h - CROP))
    p = shot[y:y + CROP, x:x + CROP]
    return p.astype(np.float32), chroma_of(p)


# 用户点名「不要识别」的格子：这些是限定物品/兑换券之类，图标跟常规物品撞得太像，
# 认出来只会把数量算错。按 (全局行, 列) 记，行列都从 0 数（面板上说的「第三行第一个」= (2,0)）。
# 只作用于「名字待定」与主表两处出口：格子本身留空，不写进报告。
SKIP_CELLS: dict[str, dict[tuple[int, int], str]] = {
    "物料": {(2, 0): "限定物品兑换券（用户 2026-11-22 点名不认）"},
    "票券": {(0, 4): "限定兑换长牌（用户 2026-11-22 点名不认）",
             (1, 4): "友情币在该格与限定物并排，用户 2026-11-22 点名不认"},
}


def _apply_skip(cells: list[dict], cat: str, row_offset: int = 0) -> None:
    """把 SKIP_CELLS 里的格子就地标成「跳过」，row_offset 用于把本屏行号换成全局行号。"""
    skip = SKIP_CELLS.get(cat) or {}
    if not skip:
        return
    for rec in cells:
        key = (rec.get("row", 0) + row_offset, rec.get("col", 0))
        if key in skip and not rec.get("skipped"):
            rec.update(name="", status="empty", skipped=True,
                       skip_reason=skip[key])


def identify(patch: np.ndarray, chroma: np.ndarray,
             icons: list[tuple[str, np.ndarray, np.ndarray]]) -> tuple[str, float, float]:
    scored = []
    for name, tb, tc in icons:
        n1 = (_ncc(patch[:, :, 0], tb[:, :, 0]) + _ncc(patch[:, :, 1], tb[:, :, 1])
              + _ncc(patch[:, :, 2], tb[:, :, 2])) / 3.0
        n2 = (_ncc(chroma[:, :, 0], tc[:, :, 0]) + _ncc(chroma[:, :, 1], tc[:, :, 1])) / 2.0
        scored.append(((1 - CHROMA_W) * n1 + CHROMA_W * n2, name))
    scored.sort(reverse=True)
    best, bname = scored[0]
    # 第二名要和第一名「不是同一张图」才算数：考核自选包I~V 五个文件一模一样，
    # 拿它们当第二名会让 margin 恒为 0，把本来分得开的格子也判成存疑。
    h = NAME_HASH.get(bname)
    margin = 0.0
    for s, n in scored[1:]:
        if h is not None and NAME_HASH.get(n) == h:
            continue
        margin = best - s
        break
    return bname, best, margin


# 词根族：名字共享 >=3 字后缀、成员 >=2 的一批同名不同款（石墨条四条、冬谷礼盒四款、
# 分析报告三种……）。用来做「族内色度复判」，见 chroma_in_word_family()。
# 排除已经在 GRADED_FAMILY 里的等级族 —— 那些由框色定档 + 档内重认负责，不能重复处理。
WORD_FAMILY: dict[str, list[str]] = {}
WORD_FAMILY_SUF = 3
CHROMA_FAMILY_GAP = 0.01   # 族内色度 top1 领先第二名多少才算「颜色给了独立证据」
_CHROMA_BY_NAME: dict[str, np.ndarray] = {}   # 名字 → 色度模板（族内复判用，load_icons 时重建）
_TPL_BY_NAME: dict[str, np.ndarray] = {}      # 名字 → RGB 模板（差异区复判用，同一处重建）


def build_word_family(icons: list[tuple[str, np.ndarray, np.ndarray]]) -> None:
    """建词根族（每次 load_icons 重建，和 FAMILY 一样要清空重填）。"""
    WORD_FAMILY.clear()
    _CHROMA_BY_NAME.clear()
    _TPL_BY_NAME.clear()
    for name, tb, tc in icons:
        _CHROMA_BY_NAME.setdefault(name, tc)
        _TPL_BY_NAME.setdefault(name, tb)
    buckets: dict[str, list[str]] = {}
    for name, _tb, _tc in icons:
        if re.sub(r"[IVX0-9]+$", "", name) in GRADED_FAMILY:
            continue
        if len(name) <= WORD_FAMILY_SUF:
            continue
        buckets.setdefault(name[-WORD_FAMILY_SUF:], []).append(name)
    for members in buckets.values():
        if len(members) < 2:
            continue
        for name in members:
            WORD_FAMILY.setdefault(name, members)


def chroma_in_word_family(patch: np.ndarray, chroma: np.ndarray, name: str,
                          icons: list[tuple[str, np.ndarray, np.ndarray]]
                          ) -> tuple[str, float]:
    """只在 name 自己的词根族里比色度 → (族内色度 top1, 它对第二名的 gap)。

    为什么要这一步：石墨条四条（鎏金/錾银/饰铜/鳞钻）是同一根笔同一个立方体框，
    只有材质颜色不同。全库按下式打分时结构项（权重 0.7）会把票投给「錾银」
    （RGB 0.593 vs 鎏金 0.494），色度项（权重 0.3）投给「鎏金」，两者抵消后
    margin 只剩 0.0166 < 0.03，被判成「名字待定」—— 名字其实认对了，只是不敢担保。

    而把候选缩到同族 4 个再单比色度，四个石墨条格子全对、gap 0.053~0.154；
    核定牌族（29 格）的 gap 只有 0.0002~0.002，正好落在 CHROMA_FAMILY_GAP 之下，
    两族差两个数量级，界线干净。实测全表 161 格：只把鎏金石墨条一格从 uncertain
    升成 hit，另 17 格 margin 抬高但状态不变，0 格改名。
    """
    members = WORD_FAMILY.get(name) or []
    if len(members) < 2:
        return "", 0.0
    scored = []
    for m in members:
        tc = _CHROMA_BY_NAME.get(m)
        if tc is None:
            continue
        n2 = (_ncc(chroma[:, :, 0], tc[:, :, 0]) + _ncc(chroma[:, :, 1], tc[:, :, 1])) / 2.0
        scored.append((n2, m))
    if len(scored) < 2:
        return "", 0.0
    scored.sort(reverse=True)
    return scored[0][1], scored[0][0] - scored[1][0]


# 差异区族：名字按「·」分族、成员 >=3、族内两两最高 NCC >= 0.9 的那批 —— 目前只有
# 「盈级装备匣·{宿卫,战略,构术,轻锐,远击}」5 个（互相 0.9555~0.9598，平均像素差 2.93）。
# 这类图标是同一个箱子的 5 个职业版，只差箱面图案；全模板 NCC 分不开，但差异区能。
DIFF_FAMILY: dict[str, list[str]] = {}          # 名字 → 族成员
DIFF_MASKS: dict[str, np.ndarray] = {}          # 族首 → TPL_SIZE×TPL_SIZE 的 uint8 判别掩膜
DIFF_FAMILY_GAP_MIN = 0.05                      # 判别区 margin 门槛（实测两格 0.21 / 0.46）
DIFF_NCC_TRIGGER = 0.90                         # 族内最高 NCC 达到这个才认为「需要判别区」


def build_diff_family(icons: list[tuple[str, np.ndarray, np.ndarray]]) -> None:
    """建「差异区族」并算出每族的判别掩膜（每次 load_icons 重建）。

    判别区的取法：把同族所有模板的不透明区叠加，逐像素量「五个模板间的极差」，
    极差 >= 20 的连通块（面积 >= 20，丢零星噪点）就是真正在区分它们的区域。
    盈级装备匣实测：3017 个不透明像素里 790 个进判别区（26%），落在
    bbox (33,20,39x32) 与 (69,20,12x10) 两处 —— 正是箱子正面的职业纹章。
    """
    DIFF_FAMILY.clear()
    DIFF_MASKS.clear()
    by_name = {n: tb for n, tb, _tc in icons}
    buckets: dict[str, list[str]] = {}
    for name, _tb, _tc in icons:
        if "·" not in name:
            continue
        buckets.setdefault(name.split("·")[0], []).append(name)
    for base, members in buckets.items():
        if len(members) < 3:
            continue
        # 只有「互像到 NCC >= 0.9」的族才需要判别区；研学报告那种 0.4972 本来就能分开
        peak = 0.0
        for a, b in ((members[i], members[j])
                     for i in range(len(members)) for j in range(i + 1, len(members))):
            ta, tb = by_name[a], by_name[b]
            peak = max(peak,
                       (_ncc(ta[:, :, 0], tb[:, :, 0]) + _ncc(ta[:, :, 1], tb[:, :, 1])
                        + _ncc(ta[:, :, 2], tb[:, :, 2])) / 3.0)
        if peak < DIFF_NCC_TRIGGER:
            continue
        stack = np.stack([by_name[m] for m in members])
        spread = (stack.max(axis=0) - stack.min(axis=0)).mean(axis=2)
        raw = (spread >= 20).astype(np.uint8)
        nlab, lab, stats, _ = cv2.connectedComponentsWithStats(raw, connectivity=8)
        keep = np.zeros_like(raw)
        for i in range(1, nlab):
            if stats[i, 4] >= 20:
                keep[lab == i] = 1
        if keep.sum() < 50:          # 差异区太小就不值得用
            continue
        DIFF_MASKS[base] = keep
        for m in members:
            DIFF_FAMILY[m] = members


def diff_mask_hit(patch: np.ndarray, name: str
                  ) -> tuple[str, float, float]:
    """在 name 所属的差异区族里重新判别 → (top1, margin, top1 的判别区分)。"""
    members = DIFF_FAMILY.get(name) or []
    if len(members) < 2:
        return "", 0.0, 0.0
    base = name.split("·")[0]
    mask = DIFF_MASKS.get(base)
    if mask is None:
        return "", 0.0, 0.0
    m = mask.astype(bool)
    mh, mw = mask.shape
    scored = []
    for n in members:
        tc = _TPL_BY_NAME.get(n)
        if tc is None:
            continue
        # 先用整模板定位（滑动搜索），再在落点上只比判别区 —— 判别区在模板坐标里固定，
        # 直接拿模板中心去对会被「图标在格子里没有居中」这一点带偏。
        best_pos, best_val = (0, 0), -2.0
        for ch in range(3):
            res = cv2.matchTemplate(patch[:, :, ch], tc[:, :, ch], cv2.TM_CCOEFF_NORMED)
            _, mx, _, pos = cv2.minMaxLoc(res)
            if mx > best_val:
                best_val, best_pos = mx, pos
        x, y = best_pos
        if y + mh > patch.shape[0] or x + mw > patch.shape[1]:
            continue
        sub = patch[y:y + mh, x:x + mw]
        vals = []
        for ch in range(3):
            pv = sub[:, :, ch][m].astype(np.float64)
            tv = tc[:, :, ch][m].astype(np.float64)
            pv = pv - pv.mean()
            tv = tv - tv.mean()
            den = float(np.sqrt((pv * pv).sum() * (tv * tv).sum()))
            vals.append(float((pv * tv).sum() / den) if den > 1e-6 else 0.0)
        scored.append((sum(vals) / 3.0, n))
    if len(scored) < 2:
        return "", 0.0, 0.0
    scored.sort(reverse=True)
    return scored[0][1], scored[0][0] - scored[1][0], scored[0][0]


def build_grade_candidates(icons: list[tuple[str, np.ndarray, np.ndarray]]
                           ) -> dict[str, list[tuple[str, np.ndarray, np.ndarray]]]:
    """按等级把图标分成 5 组：{"I": [8 个带 I 后缀的精确名], "II": [...], ...}。

    后缀必须整段等于罗马数字才收 —— 用 endswith 会把「核定牌自选包II」错收进 "I" 档。
    """
    out: dict[str, list[tuple[str, np.ndarray, np.ndarray]]] = {g: [] for g in GRADE_SUFFIX}
    for name, tb, tc in icons:
        base = re.sub(r"[IVX0-9]+$", "", name)
        if base not in GRADED_FAMILY:
            continue
        suffix = name[len(base):]
        if suffix in out:
            out[suffix].append((name, tb, tc))
    return out


def identify_in_grade(patch: np.ndarray, chroma: np.ndarray,
                      cand: list[tuple[str, np.ndarray, np.ndarray]]
                      ) -> tuple[str, float, float] | None:
    """只在一个等级档的候选里认名字 → (精确名, score, margin)。

    为什么要再认一次：核定牌 / 资质证明 / 自选包的图标「同族不同档」之间极像，
    全库比对时第二名常常是同一族的邻档，margin 只有 0.005~0.06，拿 margin 当可信度
    会把本来认对的格子误杀成「存疑」（2026-11-22 18:54 用户反馈的那批就是）。
    等级已由外框色定死（标定集资质证明 I~V 五格 5/5 全对），候选集缩到同档 8 个精确名
    之后，top1 只需区分职业，不再承担分档的活。
    """
    if not cand:
        return None
    scored = []
    for name, tb, tc in cand:
        n1 = (_ncc(patch[:, :, 0], tb[:, :, 0]) + _ncc(patch[:, :, 1], tb[:, :, 1])
              + _ncc(patch[:, :, 2], tb[:, :, 2])) / 3.0
        n2 = (_ncc(chroma[:, :, 0], tc[:, :, 0]) + _ncc(chroma[:, :, 1], tc[:, :, 1])) / 2.0
        scored.append(((1 - CHROMA_W) * n1 + CHROMA_W * n2, name))
    scored.sort(reverse=True)
    best, bname = scored[0]
    h = NAME_HASH.get(bname)
    margin = 0.0
    for s, n in scored[1:]:
        if h is not None and NAME_HASH.get(n) == h:
            continue
        margin = best - s
        break
    return bname, best, margin


# ---------- 等级外框：格子四边那圈彩色描边决定等级（用户 2026-11-22 定的映射） ----------

# 灰=Ⅰ 绿=Ⅱ 蓝=Ⅲ 金=Ⅳ 红=Ⅴ
GRADE_OF_COLOR = {"灰": "I", "绿": "II", "蓝": "III", "金": "IV", "红": "V"}
FRAME_SEG = 30            # 每条边只取中段 2*FRAME_SEG 长，避开圆角与被相邻格污染的两端
FRAME_VIVID_S, FRAME_VIVID_V = 120, 100   # 描边实测 S 190~255、V 130~255；格内图标也鲜，但不能取整格


def frame_pixels(shot: np.ndarray, x: int, y: int) -> np.ndarray:
    """取这一格四条边的外框描边像素（BGR，N×3）。

    描边几何实测（相对格左上角）：左 x∈[-4,-1]、右 x∈[+99,+102]、
    上 y∈[-3,-1]、下 y∈[+100,+102]，线宽 3~4px。
    """
    h, w = shot.shape[:2]
    segs = []
    for x0, x1 in ((x - 4, x - 1), (x + 99, x + 102)):      # 左右竖边
        for yy in range(y + 20, y + 20 + FRAME_SEG * 2):
            if 0 <= yy < h and 0 <= x0 and x1 <= w:
                segs.append(shot[yy, max(0, x0):min(w, x1)])
    for y0, y1 in ((y - 3, y - 1), (y + 100, y + 102)):      # 上下横边
        for xx in range(x + 20, x + 20 + FRAME_SEG * 2):
            if 0 <= xx < w and 0 <= y0 and y1 <= h:
                segs.append(shot[max(0, y0):min(h, y1), xx])
    if not segs:
        return np.zeros((0, 3), np.uint8)
    return np.concatenate([s.reshape(-1, 3) for s in segs if s.size], axis=0)


def grade_of(px: np.ndarray) -> tuple[str, str, float]:
    """外框像素 → (颜色名, 罗马数字档, 置信度)。判不出来返回 ("?", "", 0.0)。

    为什么要按外框色而不是图标：核定牌 I~V / 资质证明 I~V 的图标虽然不同，
    但低档之间 NCC 只有 0.10~0.24，top1 常错到邻档；两个自选包 I~V 的图标
    更是逐字节相同（md5 一样），图标永远分不出档。外框色是游戏直接给的答案，
    标定集（资质证明 I~V 五格，格子上印着罗马数字）实测 5/5 全对。
    """
    if px is None or len(px) < 100:
        return "?", "", 0.0
    hsv = cv2.cvtColor(px.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    hh, ss, vv = hsv[:, 0].astype(int), hsv[:, 1].astype(int), hsv[:, 2].astype(int)
    vivid = (ss >= FRAME_VIVID_S) & (vv >= FRAME_VIVID_V)
    nv = int(vivid.sum())
    frac = nv / float(len(px))
    s_med = float(np.median(ss))
    # 灰框（Ⅰ 档）是低饱和银灰，两条判据都要留：物料行8列5/行9列6/行10列1 三格
    # frac=0.30 高于 0.25 阈值，全靠 s_med 39/42/29 < 90 才判得住。
    if frac < 0.25 or s_med < 90:
        return "灰", "I", 1.0 - frac
    hist = np.bincount(hh[vivid] // 10, minlength=18)
    k = int(hist.argmax())
    share = float(hist[k]) / float(nv)
    if k in (17, 0):
        color = "红"
    elif k in (1, 2):
        color = "金"
    elif 3 <= k <= 8:
        color = "绿"
    else:
        color = "蓝"
    return color, GRADE_OF_COLOR[color], share


BADGE_HSV_LO, BADGE_HSV_HI = (80, 120, 120), (100, 255, 255)  # 徽章底色青绿，实测落在此区间
BADGE_W_RANGE, BADGE_H_RANGE = (55, 78), (20, 32)             # 徽章外框实测恒为 65x26
BADGE_TEXT_DX = 22      # 徽章内左边 22 px 是沙漏图标，文字从这里开始
BADGE_THR = 180         # 白字芯阈值（徽章底色灰度 ~130，字芯 255）
UNIT_DIR = ROOT / "resource" / "image" / "单位"   # 天.png / 时.png
_unit_bank: dict[str, np.ndarray] | None = None


def unit_bank() -> dict[str, np.ndarray]:
    """单位字形模板（天/时…），第一次调用时从 UNIT_DIR 读进来缓存。"""
    global _unit_bank
    if _unit_bank is None:
        bank: dict[str, np.ndarray] = {}
        if UNIT_DIR.is_dir():
            for p in sorted(UNIT_DIR.glob("*.png")):
                img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    bank[p.stem] = img
        _unit_bank = bank
    return _unit_bank


def find_badge(shot: np.ndarray, r: int, c: int) -> tuple[int, int, int, int] | None:
    """在格子右上角找倒计时徽章，返回 (x, y, w, h)，没有则 None。

    徽章恒定贴在格子右上、相对格子左上偏移 (33,-7)，比格子顶部高出 7 px，
    所以搜索窗要往上开 14 px。上一格的徽章最多只会露出底部几行，会被高度下限挡掉。
    """
    x0, y0 = X0 + c * PX, Y0 + r * PY
    wx0, wx1 = x0 + 20, min(shot.shape[1], x0 + CW)
    wy0, wy1 = max(0, y0 - 14), min(shot.shape[0], y0 + 24)
    if wx1 - wx0 < 40 or wy1 - wy0 < 20:
        return None
    hsv = cv2.cvtColor(shot[wy0:wy1, wx0:wx1], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, BADGE_HSV_LO, BADGE_HSV_HI)
    n, _lab, st, _cent = cv2.connectedComponentsWithStats(mask, 8)
    for i in range(1, n):
        bx, by, bw, bh, area = (int(v) for v in st[i])
        if not (BADGE_W_RANGE[0] <= bw <= BADGE_W_RANGE[1]):
            continue
        if not (BADGE_H_RANGE[0] <= bh <= BADGE_H_RANGE[1]):
            continue
        if area < 0.40 * bw * bh:      # 右下角被数量角标啃掉一块，面积留足余量
            continue
        return wx0 + bx, wy0 + by, bw, bh
    return None


def badge_text(shot: np.ndarray, box: tuple[int, int, int, int],
               bank: dict[str, list[np.ndarray]]) -> tuple[str, int | None]:
    """徽章文字 → ("10天", 10) / ("14时", None)。

    数字复用 wh_counts 的数量模板（同一套字体）。单位字形有讲究：
    「天」是一个 16px 宽的单体，而「时」会被拆成 6px + 9px 两块，
    所以先把末位向左合并到总跨度 ≥12px，再拿 `resource/image/单位/` 里的
    单位模板比对（天/时），不认识就当没单位、只留原文。
    """
    x, y, w, h = box
    gray = cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY)
    reg = gray[y + 2:y + h - 2, x + BADGE_TEXT_DX:x + w - 2]
    if reg.size == 0:
        return "", None
    mask = (reg > BADGE_THR).astype(np.uint8) * 255
    n, _lab, st, _cent = cv2.connectedComponentsWithStats(mask, 8)
    comps = []
    for i in range(1, n):
        gx, gy, gw, gh, area = (int(v) for v in st[i])
        if gh < 10 or area < 12:
            continue
        comps.append((gx, gy, gw, gh))
    comps.sort(key=lambda t: t[0])
    if not comps:
        return "", None

    # ① 末位往左合并，直到跨度够一个汉字的宽度
    k = 1
    while k < len(comps):
        span = (comps[-1][0] + comps[-1][2]) - comps[-k][0]
        if span >= 12 or k >= 3:
            break
        k += 1
    tail = comps[-k:]
    px0, px1 = tail[0][0], tail[-1][0] + tail[-1][2]
    py0 = min(c[1] for c in tail)
    py1 = max(c[1] + c[3] for c in tail)
    patch = reg[py0:py1, px0:px1]

    # ② 认单位
    unit = None
    units = unit_bank()
    if units and patch.size:
        q = wh_counts.prep(patch)
        scores = {nm: wh_counts.sim(q, wh_counts.prep(t)) for nm, t in units.items()}
        best = max(scores, key=scores.get)
        if scores[best] >= 0.5:
            unit = best

    # ③ 剩下的都是数字
    digits = comps[:-k] if unit else comps
    txt = ""
    for gx, gy, gw, gh in digits:
        ch, _sc = wh_counts.classify(reg[gy:gy + gh, gx:gx + gw], bank)
        txt += ch
    if unit:
        txt += unit
    days = int(txt[:-1]) if unit == "天" and txt[:-1].isdigit() else None
    return txt, days


def scan_image(shot: np.ndarray, cat: str = "物料", rows: int = 4,
               shot_name: str = "<memory>", out_json: Path | None = None,
               verbose: bool = True, row_offset: int = 0,
               shot_dy: int = 0) -> dict:
    """扫一张截图 → {"ts","shot","category","icons","cells"}。

    agent 侧的「仓库抓取」自定义动作直接调这个（图从 controller.post_screencap 拿），
    命令行 main() 也走这里，保证两条路的口径完全一致。

    row_offset：这一屏的第一行对应整页的第几行（已翻过几行）。只用来把 SKIP_CELLS
    里记的全局行列换算成本屏行列 —— rec["row"] 仍存本屏行号，合并时由 merge_cells 加偏移。
    2026-11-22 17:16 那轮忘了传，结果「第三行第一个」这条规则把第 2 屏的本屏第 3 行
    （全局第 5 行）的白毫银针、以及第 3 屏的「生活贴士」一起排除了。

    shot_dy：这一屏喂进来之前被 shift_image 平移了多少像素（0 = 没平移）。只进存档，
    给 wh_report.crop_thumb 回原图裁缩略图时把偏移补回去 —— 不平移回去会裁到隔壁格的
    图标或者格间缝隙，2026-11-24 14:54 前「名字待定」的图基本都错位。
    """
    shot_gray = cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY)
    icons = _icons_cached()
    grade_cand = build_grade_candidates(icons)
    deny = _deny_cached()
    bank_digits = _bank_cached()
    skip = SKIP_CELLS.get(cat, {})

    cells, unknown = [], []
    for r in range(rows):
        for c in range(COLS):
            patch, chroma = cell_patch(shot, r, c)
            name, score, margin = identify(patch, chroma, icons)
            # 词根族内色度复判：只抬可信度，不改名字。
            # 石墨条四条结构几乎一样，全库加权的 margin 会被抵到 0.0166（<0.03 判存疑），
            # 但候选缩到同族 4 个单比色度时 gap 有 0.0533，名字本来就是对的。
            # 条件收紧到「族内色度 top1 仍是同一个名字」才采纳，避免它把名字带偏。
            fam_top, fam_gap = chroma_in_word_family(patch, chroma, name, icons)
            if fam_top == name and fam_gap >= CHROMA_FAMILY_GAP and fam_gap > margin:
                margin = fam_gap
            # 差异区复判：盈级装备匣这类「同一个图只差一小块图案」的族，整模板 NCC 0.9555
            # 分不开（margin 只剩 0.008），但只看差异区的 margin 能到 0.21 / 0.46。
            diff_top, diff_gap, diff_score = diff_mask_hit(patch, name)
            if (diff_top == name and diff_gap >= DIFF_FAMILY_GAP_MIN
                    and diff_gap > margin):
                margin = diff_gap
            cnt = "".join(wh_counts.classify(g, bank_digits)[0]
                          for _, g in wh_counts.glyphs(shot_gray, r, c))
            x, y = X0 + c * PX, Y0 + r * PY
            # 先看这一格是不是「名单里没有的物品」：是就整格不认，名字留空。
            # 为什么不用 score 阈值切：名单外那两格 0.366/0.395，名单内最差的真物品
            # 0.412，只差 0.017，硬切会误伤真物品。而拿它们的图标自己当模板去比，
            # 兑换券 0.694、紫信封 0.739，比第二名高 0.43 以上，界线干净。
            dname, dscore = _deny_hit(patch, chroma, deny)
            if dname:
                rec = {"row": r, "col": c, "x": x, "y": y, "name": "",
                       "score": round(dscore, 3), "margin": 0.0,
                       "count": cnt, "count_final": cnt,
                       "shot": shot_name, "shot_row": r, "shot_col": c,
                       "shot_dy": shot_dy,
                       "denied": dname,
                       "status": "empty",
                       "denied_note": f"名单外物品（{dname} {dscore:.2f}），按要求不识别"}
                cells.append(rec)
                continue
            rec = {"row": r, "col": c, "x": x, "y": y, "name": name,
                   "score": round(score, 3), "margin": round(margin, 3),
                   "count": cnt, "count_final": cnt,   # wh_report.py:220 读的是 count_final
                   # 存档是哪一张截图的第几行第几列：多屏合并后 row 是全局行号，
                   # 要回原图裁这一格必须靠这两个「本屏坐标」（面板里「名字待定」用它出图）
                   "shot": shot_name, "shot_row": r, "shot_col": c,
                   # 喂给 scan_image 的图若被 shift_image 平移过，回原图裁图要补回去
                   "shot_dy": shot_dy}
            if (r + row_offset, c) in skip:
                # 用户点名不认的格子（限定物品兑换券之类）：留着占位，别进报告。
                # SKIP_CELLS 记的是全局行列，这里要加上本屏偏移才等价。
                rec.update(name="", status="empty", skipped=True,
                           skip_reason=skip[(r + row_offset, c)])
                cells.append(rec)
                continue
            qty_ok = bool(QTY_RE.fullmatch(cnt))
            if not qty_ok and score < 0.60:
                # 空格：数量读不出合法数字，而且没有任何模板能贴上去。
                # 判据只用 score 不用 margin 的原因：加进 66×66 的「改名卡」小模板后，
                # 空白纸的 margin 能到 0.13~0.22（名次并不挤），旧判据会把这些格子
                # 判成「改名卡 x1」直接塞进报告（2026-11-22 14:47 那一轮就是）。
                # 实测空白纸 score 0.39~0.52、真物品最差 0.37（但数量能读出来，走下面那支）。
                rec.update(name="", status="empty")
                cells.append(rec)
                continue

            # ---- 等级族：外框色定档 → 档内 8 个精确名重认 ----
            # 两道分工：框色回答「几级」，档内比对回答「哪个职业」。
            # 全库比对时第二名常常是同族邻档，margin 0.005~0.06，靠 margin 判可信度
            # 会把认对的格子误杀成存疑；档内比对只比职业，105 格实测 105/105 对。
            base_name = re.sub(r"[IVX0-9]+$", "", name) or name
            if base_name in GRADED_FAMILY:
                color, grade, conf = grade_of(frame_pixels(shot, x, y))
                if grade:
                    rec["grade"] = grade
                    rec["grade_color"] = color
                    rec["grade_conf"] = round(conf, 2)
                    got = identify_in_grade(patch, chroma, grade_cand.get(grade) or [])
                    if got:
                        gname, gscore, gmargin = got
                        rec["identity_gap"] = round(gmargin, 3)
                        if gscore >= HIT_SCORE and qty_ok:
                            # 档内认出的职业 + 框色定的等级 = 精确名，报告按这个取图
                            name, score, margin = gname, gscore, gmargin
                            rec.update(name=gname, score=round(gscore, 3),
                                       margin=round(gmargin, 3))
                        else:
                            # 职业没认准：只报族名，等级照样标出来（图按族名+等级取）
                            rec["name"] = base_name
                            rec["grade_unknown"] = [f"{base_name}{g}" for g in GRADE_SUFFIX]
                            name, score, margin = base_name, gscore, gmargin
                            rec.update(score=round(gscore, 3), margin=round(gmargin, 3))

            dup = sorted(set(HASH_NAMES.get(NAME_HASH.get(name, ""), [])))
            if not rec.get("grade"):
                # 没被框色定档的族才需要按「同图多等级」收敛（自选包 I~V 是同一张图）。
                # 已被框色定档的格子上面已经处理过，别再拿 dup 把精确名砍回族名。
                fam = FAMILY.get(re.sub(r"[IVX0-9]+$", "", name) or name, [])
                if len(dup) > 1 or (len(fam) > 1 and margin < 0.10):
                    base = re.sub(r"[IVX0-9]+$", "", name) or name
                    rec["name"] = base
                    rec["grade_unknown"] = dup if len(dup) > 1 else fam
            if score >= HIT_SCORE and margin >= HIT_MARGIN and qty_ok:
                rec["status"] = "hit"
            elif rec.get("grade") and qty_ok and score >= HIT_SCORE:
                # 档内职业差只有 0.005~0.06（图标本身极像），margin 门槛在这里不适用：
                # 等级已经由框色担保，档内 top1 实测 105/105 全对。
                rec["status"] = "hit"
                rec["margin_note"] = f"同档 margin {margin:.3f} < {HIT_MARGIN}"
            else:
                rec["status"] = "uncertain"
                if not qty_ok:
                    # 数量读成了 ".", "万", "..4" 这类垃圾串 = 这一格其实没有数字（多半是空白纸），
                    # 名字再像也不能当真。以前这里会把这些格子判成「改名卡 x1」塞进报告。
                    rec["count_bad"] = cnt
                unknown.append(rec)
                UNKNOWN_DIR.mkdir(parents=True, exist_ok=True)
                cv2.imencode(".png", shot[y:y + CH, x:x + CW])[1].tofile(
                    str(UNKNOWN_DIR / f"unknown-{cat}-r{r}c{c}.png"))
            box = find_badge(shot, r, c)
            if box:
                txt, days = badge_text(shot, box, bank_digits)
                unit = txt[-1:] if txt else ""
                if unit in unit_bank():
                    # 认出了单位（天/时）就进报告：「14时」是有效的倒计时，
                    # 只是不足一天，不该因为 expire_days 算不出来就被丢掉。
                    rec["expire_text"] = txt
                    if days is not None:
                        rec["expire_days"] = days
                else:
                    # 读到了徽章但没认出单位（例如以后出现「永久」这类非数字倒计时）。
                    # 只留原始字串备查，不进报告，免得图墙上出现一个没单位的数字。
                    rec["expire_raw"] = txt
            cells.append(rec)

    doc = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "shot": shot_name,
           "category": cat, "icons": len(icons), "cells": cells}
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        if verbose:
            print("写出", out_json)
    if verbose:
        hits = sum(1 for c in cells if c["status"] == "hit")
        print(f"图标库={len(icons)} 格数={len(cells)} 命中={hits} 存疑={len(unknown)} "
              f"空格={sum(1 for c in cells if c['status']=='empty')}")
        for c in cells:
            tag = {"hit": "  ", "uncertain": "??", "empty": "--"}[c["status"]]
            print(f'{tag} r{c["row"]}c{c["col"]} {c["score"]:.3f}/{c["margin"]:.3f} '
                  f'{c["count"]:>7}  {c["name"]} {c.get("expire_text", "")}')
    return doc


GRID_X0, GRID_X1 = 420, 1140     # 量位移时只看网格中部，避开左侧页签和右缘装饰
PAPER_Y0 = 115                   # 纸面上沿（再往上就是「227/10000」那条顶栏）
VIEW_TOP, VIEW_BOT = Y0, 700     # 网格的可见窗口：纸框是固定的，滚的是框里的内容
BAND_Y0, BAND_H = 300, 120

# 向下翻一屏的拖拽参数：x=490 是第 1、2 列之间的空隙，整条路径都走空白，不会点到物品
SCROLL_BEGIN = (490, 620)
SCROLL_END = (490, 355)
SCROLL_MS = 900                  # 拖慢一点，减少惯性甩过头（真机 265px 的拖拽曾甩出 393px）
BOTTOM_PX = 30                   # 位移小于这个就认为「这一页已经到底」
SAME_PAGE_MIN = 0.92             # 整片对齐后相似度低于此值 = 不是同一页（实测滚动 1.000、切页 0.43~0.78）
PHASE_Y0, PHASE_Y1 = 100, 700    # 量格子竖排相位时看的纵向范围（避开顶栏与纸边）


def page_similarity(prev: np.ndarray, cur: np.ndarray, shift_px: int) -> float:
    """把 cur 按 shift_px 对齐到 prev 之后，比网格区域的相似度（−1~1）。

    `measure_shift` 用的是一条横带，两个**不同**页面之间的横带也能找到 0.8 的假匹配
    （格子布局一样、图标不一样时照样能对上纸纹）。所以合并前必须再比一次整片：
    真·同一页滚动的相似度在 0.98 以上，切页只有 0.5~0.7。
    """
    gp = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gc = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # 纸框是固定的，滚的只是框里的内容 → 两端都按「可见窗口」夹，窗口外的行在两屏里
    # 画的是不同的东西（一屏是页眉、一屏是内容），拿进来比只会把相似度压低。
    y0 = max(VIEW_TOP, VIEW_TOP + shift_px)
    y1 = min(VIEW_BOT, VIEW_BOT + shift_px)
    if y1 - y0 < 150:
        return 0.0
    a = gp[y0:y1, GRID_X0:GRID_X1]
    b = gc[y0 - shift_px:y1 - shift_px, GRID_X0:GRID_X1]
    if a.shape != b.shape or a.size == 0:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom else 0.0


def shift_image(shot: np.ndarray, dy: int) -> np.ndarray:
    """把整幅画面竖着平移 dy 像素（正数=内容下移），用来抵消滚动没对齐整行的零头。

    真机那次滚了 393px = 3 行 + 9px，格子整体比锚点高了 9px：图标还能认（crop 有 20px 余量），
    但数量数字的取字窗口是死的，9px 就足以把整列数字切没了。这里先把画面推回格位再扫。
    """
    if dy == 0:
        return shot
    m = np.float32([[1, 0, 0], [0, 1, dy]])
    return cv2.warpAffine(shot, m, (shot.shape[1], shot.shape[0]),
                          flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE)


def align(prev: np.ndarray, cur: np.ndarray) -> tuple[int, float, float]:
    """求两屏位移，并用整片相似度自证，返回 (shift_px, 横带匹配度, 整片相似度)。

    单靠横带会翻车：真机滚了 3 行（393px）时，y=300 那条带在前一张里已经没有落点
    （393+300 超出画面），`matchTemplate` 就给了个 265px 的假峰值（匹配 0.859）。
    所以带子要贴着窗口上沿取，并且每个候选都必须用 `page_similarity` 复核一遍，
    相似度够高（≥0.95）就提前收工。
    """
    gp = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    gc = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)
    hay = gp[:, GRID_X0:GRID_X1]
    best = (0, 0.0, -1.0)
    for band_y in (140, 190, 240, 300):
        tpl = gc[band_y:band_y + BAND_H, GRID_X0:GRID_X1]
        if tpl.size == 0 or float(tpl.std()) < 5:
            continue                      # 平带（滚到底只剩白纸）会给出假位置
        if tpl.shape[0] >= hay.shape[0]:
            continue
        res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
        _min, maxv, _minloc, maxloc = cv2.minMaxLoc(res)
        shift = int(maxloc[1]) - band_y
        sim = page_similarity(prev, cur, shift)
        if sim > best[2]:
            best = (shift, float(maxv), sim)
        if sim >= 0.95:
            break
    return best


def measure_shift(prev: np.ndarray, cur: np.ndarray) -> tuple[int, float]:
    """只取位移和横带匹配度（给调试脚本用；正式合并走 align()）。"""
    shift, match, _sim = align(prev, cur)
    return shift, match


def row_phase(im: np.ndarray) -> tuple[int, float]:
    """量这一屏里格子竖排的「相位」：格子行起点 mod PY（相对整幅图坐标）。

    为什么要量相位、而不是只靠两屏位移取整：真机每次拖拽滚的不是整行（360/364/356px，
    行距 128），位移取整会把小数部分扔掉，滚到第三屏就攒成半行错位——图标还对得上
    （crop 有 20px 余量），数量数字却整列偏移，全读成空。相位是绝对值，每屏单独量，
    不会累积。

    做法：格子上沿是横向强边缘，把网格区的纵向梯度按行求和，对 φ∈0..127 累加
    profile[φ+128k]，峰值就是行起点相位。
    """
    g = cv2.cvtColor(im[PHASE_Y0:PHASE_Y1, GRID_X0:GRID_X1],
                     cv2.COLOR_BGR2GRAY).astype(np.float32)
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    prof = gy.sum(axis=1)
    height = len(prof)
    best, best_phi = -1.0, 0
    for phi in range(PY):
        idx = np.arange(phi, height, PY)
        s = float(prof[idx].sum())
        if s > best:
            best, best_phi = s, phi
    return (best_phi + PHASE_Y0) % PY, best


def phase_dy(ref_phase: int, phase: int) -> int:
    """把本屏推回参考相位要移动多少像素（取最近的整圈，范围 ±PY/2）。"""
    return ((ref_phase - phase + PY // 2) % PY) - PY // 2


def merge_cells(base: list[dict], new: list[dict], shift_rows: int
                ) -> tuple[list[dict], list[dict]]:
    """把「下一屏」的格子按行偏移并进「上一屏」，返回 (合并后, 冲突列表)。

    同一格（全局行, 列）被两屏都看到时：非空压空；两屏都认出名字但不一致 → 记冲突，
    保留上一屏的判定（它一定在屏幕中央，字形与图标都没被切）。
    """
    grid: dict[tuple[int, int], dict] = {}
    for c in base:
        grid[(c["row"], c["col"])] = dict(c)
    conflicts = []
    for c in new:
        g = dict(c)
        g["row"] = c["row"] + shift_rows
        g["y"] = Y0 + g["row"] * PY
        key = (g["row"], g["col"])
        old = grid.get(key)
        if old is None:
            grid[key] = g
            continue
        if old.get("status") == "empty" and g.get("status") != "empty":
            grid[key] = g
        elif old.get("status") != "empty" and g.get("status") != "empty":
            if old.get("name") != g.get("name"):
                conflicts.append({"row": g["row"], "col": g["col"],
                                  "prev": old.get("name"), "now": g.get("name"),
                                  "prev_score": old.get("score"), "now_score": g.get("score")})
    merged = [grid[k] for k in sorted(grid)]
    return merged, conflicts


def merge_into(path: Path, doc: dict, shot: np.ndarray,
               prev_shot: np.ndarray | None,
               pre: tuple[int, float, float] | None = None,
               offset_rows: int | None = None) -> dict:
    """把新一屏并进已存在的 JSON（同分类、同一页、已向下翻过一屏）。

    位移不是整行时会照并，但把残差记进 meta —— 残差大说明拖拽距离没对齐，
    这时边缘那些被切一半的格子不可信，要回头看 shift_residual。
    """
    doc.setdefault("meta", {})
    old = None
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            old = None
    if not old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return doc

    if pre is not None:
        shift_px, match, sim = pre
    elif prev_shot is None:
        shift_px, match, sim = 0, 0.0, 0.0
    else:
        shift_px, match, sim = align(prev_shot, shot)
    if pre is not None or prev_shot is not None:
        if sim < SAME_PAGE_MIN:
            # 横带能骗人：两个不同的页面（格子布局一样、图标不一样）也能给出 0.8 的假匹配。
            # 整片对齐后比一次，真·滚动 ≈0.96~1.0，切页只有 0.4~0.8，这里必须拦住。
            raise RuntimeError(
                f"两屏对不齐（位移 {shift_px}px、横带 {match:.2f}、整片相似 {sim:.3f} "
                f"< {SAME_PAGE_MIN}），这不是同一页在滚动，拒绝合并")
    shift_rows = int(round(shift_px / PY))
    residual = shift_px - shift_rows * PY
    # 这是「上一屏 → 这一屏」这一次的位移；并进已经累积过多屏的文档时，
    # 要用累积偏移（否则第三屏会把整片往下错位一整行，见 debug\_loop_test.py 场景 1）
    meta = old.get("meta", {})
    base_offset = int(meta.get("offset_rows", 0))
    total_offset = base_offset + shift_rows if offset_rows is None else int(offset_rows)
    merged, conflicts = merge_cells(old.get("cells", []), doc.get("cells", []), total_offset)
    # 再兜一次「点名不认」的格子：scan_image 只看得见一屏、按本屏行列排的，
    # 而 SKIP_CELLS 记的是全局行列；这里 cells 里的 row 已经是全局行号，
    # 所以 row_offset 传 0。多屏合并路径（agent 的慢滑、_rebuild_from_shots）都走这里。
    _apply_skip(merged, doc.get("category") or "", 0)

    meta.setdefault("shots", [old.get("shot", "?")]).append(doc.get("shot", "?"))
    meta["offset_rows"] = total_offset
    meta["shift_px"] = shift_px
    meta["shift_rows"] = shift_rows
    meta["shift_residual"] = residual
    meta["match"] = round(match, 3)
    meta["page_sim"] = round(sim, 3)
    meta["merged_cells"] = len(merged)
    if conflicts:
        meta["conflicts"] = conflicts
    doc = dict(doc)
    doc["cells"] = merged
    doc["meta"] = meta
    doc["shot"] = doc.get("shot", "?")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    shot_path = Path(argv[1])
    if not shot_path.is_absolute():
        shot_path = SHOT_DIR / shot_path
    cat, out_json, rows = "物料", None, 4
    merge = False
    i = 2
    while i < len(argv):
        if argv[i] == "--cat":
            cat, i = argv[i + 1], i + 2
        elif argv[i] == "--json":
            out_json, i = Path(argv[i + 1]), i + 2
        elif argv[i] == "--rows":
            rows, i = int(argv[i + 1]), i + 2
        elif argv[i] == "--merge":
            merge, i = True, i + 1
        else:
            i += 1

    shots = [shot_path]
    if merge:
        # 多张：按顺序把后续每张并进第一张（第一张就是这一页的第一屏）
        idx = argv.index("--merge")
        shots = [shot_path] + [Path(a) if Path(a).is_absolute() else SHOT_DIR / a
                               for a in argv[idx + 1:] if not a.startswith("--")]
    shot = _read_color(shots[0])
    if shot is None:
        print("读不到截图:", shots[0])
        return 1
    doc = scan_image(shot, cat, rows, shots[0].name, None if merge else out_json)
    prev = shot
    for p in shots[1:]:
        nxt = _read_color(p)
        if nxt is None:
            print("读不到截图:", p)
            return 1
        d2 = scan_image(nxt, cat, rows, p.name, None, verbose=False)
        if out_json:
            # 先把第一屏落到盘上，再逐步并入，失败时盘上留下的是上一屏而不是空文件
            if not out_json.exists():
                out_json.parent.mkdir(parents=True, exist_ok=True)
                out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
            doc = merge_into(out_json, d2, nxt, prev)
            m = doc["meta"]
            print(f"并入 {p.name}: 位移 {m['shift_px']}px = {m['shift_rows']} 行 "
                  f"(残差 {m['shift_residual']}px, 匹配 {m['match']}) "
                  f"→ {m['merged_cells']} 格"
                  + (f"，冲突 {len(m['conflicts'])} 处" if m.get("conflicts") else ""))
        prev = nxt
    if out_json and not merge:
        print("写出", out_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
