"""仓库快照报告：把 data/warehouse/*.json 渲染成一份 Markdown 卡片网格。

用法:
    python tools/wh_report.py                 # 三类全出，写 report/仓库快照.md
    python tools/wh_report.py --cat 物料 票券  # 只出指定分类

出口只有 Markdown 一份（用户 2026-11-22 定：HTML 图墙不做）。客户端任务面板
直接把这个 .md 当说明显示，Markdown.Avalonia 的 HtmlPlugin 只认 img/br/hr/sub/sup/span，
所以卡片是「表格单元格 + <br> + <img>」拼出来的，没有 CSS。

缩略图来源优先级:
    1. resource/image/物品/<name>.png     （图鉴图标，最干净）
    2. 名称归一后（罗马数字 / 连字符）再匹配图鉴
    3. difflib 相似度 >=0.86 的图鉴图
    4. 都没有 → 从原截图裁下这一格（并在卡片上标「⚠」）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WH_DIR = ROOT / "data" / "warehouse"
IMG_DIR = ROOT / "resource" / "image"
OUT_DIR = ROOT / "report"

CATS = ("物料", "资源", "票券")
CAT_COLOR = {"物料": "#7cc4ff", "资源": "#9ae6b4", "票券": "#ffd479"}
QUALITY_COLOR = {"耀": "#d9b45b", "炽": "#e0703f", "盈": "#57a6c4"}

ROMAN_TO_LATIN = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V", "Ⅵ": "VI"}
CN_DASH = "一"

# 图鉴分类覆盖表：图鉴里怎么归，不代表用户想在哪一类看到它。
# 用户 2026-11-22 明确要求：三个珀片归**资源**（游戏里就摆在资源页），
# 而 data/codex.json 里它们的 cat 是「物料」。这里不改图鉴数据（那是从 xlsx 转来的
# 原始资料，改了以后对不上账），只在出报告时按用户的说法覆盖。
CAT_OVERRIDE = {
    "焕光珀片": "资源",
    "锁嵌珀片": "资源",
    "移星珀片": "资源",
}

# 等级（外框色）的显示名，用来在卡片上写「Ⅳ」这一档。
GRADE_LABEL = {"I": "Ⅰ", "II": "Ⅱ", "III": "Ⅲ", "IV": "Ⅳ", "V": "Ⅴ"}


# ---------- 名称与数量 ----------

def norm_name(t: str) -> str:
    """归一化：全角→半角、罗马数字→拉丁、把被 OCR 读成「一」的连字符还原。"""
    t = unicodedata.normalize("NFKC", (t or "").strip())
    for u, a in ROMAN_TO_LATIN.items():
        t = t.replace(u, a)
    m = re.match(r"^(\S{1,4})" + CN_DASH + r"(\S{1,10})$", t)
    if m and not re.search(r"\d", m.group(1)):
        t = m.group(1) + "-" + m.group(2)
    return t


def parse_qty(t: str) -> float:
    """'1.39万' → 13900；'874974' → 874974；空 → 0。"""
    if not t:
        return 0.0
    t = str(t).strip()
    m = re.fullmatch(r"([\d.]+)\s*万", t)
    if m:
        return float(m.group(1)) * 10000
    d = re.sub(r"[^\d.]", "", t)
    if not d or d == ".":
        return 0.0
    try:
        return float(d)
    except ValueError:
        return 0.0


def fmt_qty(v: float) -> str:
    if v >= 10000:
        s = f"{v / 10000:.2f}".rstrip("0").rstrip(".")
        return f"{s}万"
    return str(int(v)) if v == int(v) else f"{v:g}"


# ---------- 图鉴索引 ----------

def icon_index() -> dict[str, Path]:
    """方形且够大的物品图。

    优先用 image\\物品\\ 里的游戏图标（128x128，格子里的样子），再兜底 image 根目录
    和 道具\\ 子目录里的图鉴插图；同名时先到的胜出，所以物品图标排在最前。
    """
    out = {}
    dirs = [IMG_DIR / "物品", IMG_DIR]
    for d in dirs:
        # 物品\\ 是我们自己裁的图标目录，允许小尺寸（改名卡 66x66、冬谷币2 60x64 是从
        # 游戏截图里抠的），根目录/道具\\ 仍按 ≥90 过滤，避免把零碎图收进来
        floor = 56 if d.name == "物品" else 90
        for p in sorted(d.glob("*.png")):
            try:
                with Image.open(p) as im:
                    w, h = im.size
            except Exception:
                continue
            if w == h or (floor == 56 and abs(w - h) <= 6 and min(w, h) >= floor):
                if w >= floor and p.stem not in out:
                    out[p.stem] = p
    for p in sorted((IMG_DIR / "道具").glob("*.png")):
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            continue
        if w == h and w >= 90 and p.stem not in out:
            out[p.stem] = p
    return out


def catalog_index() -> dict[str, dict]:
    """物品名字字典，两个来源合并：

    1. 《物华弥新-物品图鉴.xlsx》导出的 catalog.json —— 有品质/用途/描述
    2. resource/image/物品/*.png 的文件名 —— 只有名字和图标，但是游戏里真实存在的物品

    只收 xlsx 会让「六方晶石」「等轴晶石」「征集信」这类明明有图的物品被标成「图鉴未收录」。
    """
    out: dict[str, dict] = {}
    p = ROOT / "data" / "catalog.json"
    if p.exists():
        try:
            items = json.loads(p.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            items = []
        for it in items:
            name = (it.get("name") or "").strip()
            if name:
                out[norm_name(name)] = it
    icon_dir = IMG_DIR / "物品"
    if icon_dir.exists():
        for f in sorted(icon_dir.glob("*.png")):
            key = norm_name(f.stem)
            if key and key not in out:
                out[key] = {"name": f.stem, "from": "icon"}
    return out


def alias_index() -> dict[str, str]:
    """OCR 读错的名字 -> 真名（data/name_alias.json）。"""
    p = ROOT / "data" / "name_alias.json"
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("alias", {})
    except Exception:
        return {}
    return {norm_name(k): v for k, v in raw.items() if isinstance(v, str)}


def pick_icon(name: str, icons: dict[str, Path]) -> tuple[Path | None, str]:
    """按名字找图标。物品\\ 目录（128x128 游戏内图标）优先于根目录（113x113 图鉴插图）。

    两边的罗马数字写法不一致（物品\\ 用 Ⅱ\\Ⅲ，根目录用 II\\III），归一化后会撞同名，
    所以先按「物品\\ 目录 + 归一化」查一遍，再去走通用路径，否则 exact 会先命中根目录那张。
    """
    if not name:
        return None, "none"
    key = norm_name(name)
    for k, p in icons.items():
        if p.parent.name == "物品" and norm_name(k) == key:
            return p, "item-norm"
    if name in icons:
        return icons[name], "exact"
    by_norm = {norm_name(k): k for k in icons}
    if key in by_norm:
        return icons[by_norm[key]], "norm"
    best, best_r = None, 0.0
    for k in icons:
        r = SequenceMatcher(None, key, norm_name(k)).ratio()
        if r > best_r:
            best, best_r = k, r
    if best and best_r >= 0.86:
        return icons[best], f"fuzzy {best_r:.2f}"
    return None, "none"


def thumb_data_uri(path: Path | None, name: str, size: int = 128) -> str:
    if path is None:
        return ""
    with Image.open(path) as im:
        im = im.convert("RGBA").resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def placeholder_color(name: str) -> str:
    h = sum((i + 1) * ord(c) for i, c in enumerate(name or "?"))
    hue = h % 360
    return f"hsl({hue} 32% 26%)"


# ---------- 任务说明用的卡片网格（Markdown） ----------

THUMB_DIR = OUT_DIR / "icons"
THUMB_PX = 64          # 面板里的图标就这么大，别指望 Markdown 能缩放


def thumb_file(path: Path | None, name: str, px: int = THUMB_PX) -> str | None:
    """把图标落成一张小图，返回给 Markdown 用的绝对路径。

    Markdown.Avalonia 只认绝对路径，而且不认 `width=` 之类的 HTML 尺寸，
    所以尺寸必须在文件里就定好。文件名用哈希，避开中文/空格/括号。
    """
    if path is None:
        return None
    # 键里必须带源图身份：光用 name 的话，源图从 image\ 换成 image\物品\ 后
    # 文件名不变，而下面的 mtime 比较会认为缓存还新，面板就一直显示旧图。
    try:
        src_id = f"{path.relative_to(ROOT).as_posix()}|{path.stat().st_size}"
    except ValueError:
        src_id = f"{path}|{path.stat().st_size}"
    stem = hashlib.sha1(f"{name}|{px}|{src_id}".encode("utf-8")).hexdigest()[:12]
    out = THUMB_DIR / f"{stem}.png"
    if not out.exists() or out.stat().st_mtime < path.stat().st_mtime:
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as im:
            im = im.convert("RGBA")
            box = im.getbbox()
            if box:
                im = im.crop(box)
            im.thumbnail((px, px), Image.LANCZOS)
            canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            canvas.paste(im, ((px - im.width) // 2, (px - im.height) // 2), im)
            canvas.save(out, format="PNG", optimize=True)
    return str(out).replace("\\", "/")


def crop_thumb(cell: dict, px: int = THUMB_PX) -> str | None:
    """从原始截图里把这一格裁出来当缩略图（给「名字待定」用）。

    认错名字的格子如果还拿「认错的那个图标」当图，等于二次误导；直接从截图裁，
    用户看到的就是游戏里那块东西本来的样子。

    两个坑（2026-11-24 14:54 修）：
    1. `shot` 以前存的是 `live-1` 这种代号，拼 debug/shots/live-1 必然不存在，
       于是每张待定卡都只剩一个点。现在 warehouse_agent._screencap 落盘后
       把真实文件名一路传进 scan_image，这里才拼得对。
    2. 多屏合并时喂给扫描的是 `W.shift_image(nxt, dy)` 平移过的图，格位按平移后的
       画面算。回原图裁图必须把 dy 补回去，否则第 2 屏起最多偏 48px（能裁到隔壁格
       或格间缝隙）。dy 记在 rec["shot_dy"] 里。老 JSON 没这个字段，按 0 处理。
    """
    shot = cell.get("shot")
    sr, sc = cell.get("shot_row"), cell.get("shot_col")
    if not shot or sr is None or sc is None or shot.startswith("<"):
        return None
    src = ROOT / "debug" / "shots" / shot
    if not src.exists():
        return None
    import wh_scan  # 同目录，格子几何只认这一处，别复制常量

    dy = int(cell.get("shot_dy") or 0)
    stem = hashlib.sha1(f"crop|{shot}|{sr}|{sc}|{dy}|{px}".encode("utf-8")).hexdigest()[:12]
    out = THUMB_DIR / f"{stem}.png"
    if not out.exists():
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            x = wh_scan.X0 + sc * wh_scan.PX
            # shift_image(im, dy) 让内容整体下移 dy，所以原图里的位置 = 平移后位置 - dy
            y = wh_scan.Y0 + sr * wh_scan.PY - dy
            box = (max(0, x), max(0, y),
                   min(im.width, x + 106), min(im.height, y + 106))
            im = im.crop(box)
            im.thumbnail((px, px), Image.LANCZOS)
            canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            canvas.paste(im, ((px - im.width) // 2, (px - im.height) // 2))
            canvas.save(out, format="PNG", optimize=True)
    return str(out).replace("\\", "/")


def codex_index() -> dict[str, dict]:
    """图鉴索引：名字 → {id, cat, quality}（来自三份 xlsx 转出的 data/codex.json）。"""
    p = ROOT / "data" / "codex.json"
    if not p.exists():
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    idx: dict[str, dict] = {}
    for it in doc.get("items", []):
        idx.setdefault(it["name"], it)
        idx.setdefault(it["name"].casefold(), it)
    return idx


def _expire_key(ex: str) -> tuple:
    """倒计时的排序键：先按「时」再按「天」的天数，没倒计时的排最前。

    「13时」比「7天」先到期，所以时单位要排在天单位前面。
    """
    if not ex:
        return (0, 0)
    unit = ex[-1:]
    num = ex[:-1]
    try:
        v = float(num)
    except ValueError:
        return (9, 0)
    return (1, v) if unit == "时" else (2, v)


def _grade_key(g: str) -> int:
    """等级排序键：没等级排最前，Ⅰ~Ⅴ 依次。"""
    return {"": 0, "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}.get(g or "", 6)


def render_md_grid(docs: list[dict], icons: dict[str, Path],
                   catalog: dict[str, dict] | None = None,
                   aliases: dict[str, str] | None = None,
                   cols: int = 5,
                   codex: dict[str, dict] | None = None) -> str:
    """按「图标 + 名字 + 数量」的卡片网格出 Markdown（给客户端任务说明用）。

    客户端的 Markdown 引擎带 HtmlPlugin，`<br>` 会换行、`<img>` 会出图，
    但表格单元格里塞不了真正的 CSS 卡片，所以用「一张表格 = 若干行格子」近似。

    排版规矩（用户 2026-11-22 定）：
    - 每类只写分类名（物料 / 资源 / 票券），不写「N 种 / N 格 / 时间」；
    - **分类以图鉴为准**，看图鉴之前先过一遍 CAT_OVERRIDE：三个珀片图鉴写着物料，
      但用户要求归资源（游戏里就摆在资源页），以用户口径为准；
    - 类内按图鉴物品 ID 升序，图鉴没有的排最后；
    - 同名不同倒计时的**分开放**（不同时长的冬谷神秘水不是同一堆，数量不能相加）；
    - 同名不同等级的**也分开放**：考核自选包 Ⅱ 和 Ⅳ 是两种东西，不能合成一张卡；
    - 名字没把握的格子不进主表（会把数量算错），末尾单列「名字待定」并直接从原图裁图。
    """
    catalog = catalog or {}
    aliases = aliases or {}
    codex = codex if codex is not None else codex_index()

    out = ["# 仓库快照", ""]
    pend: dict[str, list[dict]] = {}
    # 图鉴里没有的东西（改名卡）按「主要出现在哪一页」归属。
    # 用户 2026-11-22：改名卡属于物料 —— 在票券页认出改名卡是误判，不该挂到票券下面。
    home: dict[str, str] = {}
    for doc in docs:
        for c in doc["cells"]:
            if c.get("status") != "hit":
                continue
            n = aliases.get(norm_name(c.get("name") or ""), norm_name(c.get("name") or ""))
            if n:
                home[n] = home.get(n) or doc.get("category", "?")

    # 第一遍：把所有有把握的格子按「图鉴分类」归桶
    buckets: dict[str, list[tuple]] = {}
    order: list[str] = [d.get("category", "?") for d in docs]
    for doc in docs:
        page = doc.get("category", "?")
        pending: list[dict] = []
        for c in doc["cells"]:
            if c.get("skipped"):
                # 用户点名不认的格子（限定兑换券之类）：连「名字待定」都不进。
                continue
            if c.get("denied"):
                # 名单外物品（兑换券、紫信封…）：wh_scan 已经用负样本认出来是「不在图鉴
                # 里的东西」。用户 2026-11-24 15:42 定：不用报告也不用识别 —— 连一节
                # 「名单外」都不出，跟没扫到这一格一样静默。
                continue
            if c.get("status") != "hit":
                # 只把「有把握」的格子并进同名数量：存疑的名字合并会把数量算错
                # （物料页那个「改名卡」其实是兑换券，合并后变成 2）。
                # 但它们不能就此消失——统一收进末尾「名字待定」一节。
                if c.get("status") != "empty":
                    pending.append(c)
                continue
            name = norm_name(c.get("name") or "")
            name = aliases.get(name, name)          # 名卡→改名卡、冬谷币2→冬谷币…
            if not name:
                pending.append(c)
                continue
            cx = codex.get(name) or codex.get(name.casefold())
            if CAT_OVERRIDE.get(name):
                cat = CAT_OVERRIDE[name]             # 用户口径优先于图鉴（珀片→资源）
            elif cx and cx.get("cat"):
                cat = cx["cat"]                      # 以图鉴为准
            elif home.get(name):
                cat = home[name]                     # 图鉴没有的按主场页
            else:
                cat = page
            qty = parse_qty(c.get("count_final") or "")
            qty_txt = c.get("count_final") or ""
            iid = cx.get("id") if cx else None
            # 等级：外框色判出来的（c["grade"]，Ⅰ~Ⅴ）与「图标同图分不出档」
            # （c["grade_unknown"]）是两件不同的事，分开传下去。
            grade = c.get("grade") or ""
            gu = bool(c.get("grade_unknown")) and not grade
            buckets.setdefault(cat, []).append(
                (iid if isinstance(iid, int) else 10 ** 9,
                 name, qty, qty_txt, c.get("expire_text") or "", grade, gu))
            if cat not in order:
                order.append(cat)
        if pending:
            pend.setdefault(page, []).extend(pending)

    # 第二遍：逐类出表
    for cat in order:
        rows = buckets.get(cat) or []
        if not rows:
            continue
        # 同名合并：**到期日不同的不合并**（用户 2026-11-22：不同时长的冬谷神秘水要分开）。
        # **等级不同的也不合并**：考核自选包 Ⅱ 和 Ⅳ 是两种东西，数量各自算。
        # 只有「名字 + 倒计时 + 等级」都相同才是同一堆，数量才相加。
        merged: dict[tuple[str, str, str], dict] = {}
        for iid, name, qty, qty_txt, ex, grade, gu in rows:
            m = merged.setdefault((name, ex, grade or ("?gu" if gu else "")),
                                  {"id": iid, "name": name, "qty": 0.0,
                                   "one": qty_txt, "ex": ex,
                                   "grade": grade, "gu": gu})
            m["qty"] += qty
        ordered = sorted(merged.values(),
                         key=lambda m: (m["id"], m["name"], _expire_key(m["ex"]),
                                        _grade_key(m["grade"])))

        out.append(f"## {cat}")
        out.append("")
        cells = []
        for m in ordered:
            name = m["name"]
            img = thumb_file(pick_icon(name, icons)[0], name)
            qty = fmt_qty(m["qty"])
            ex = f" ⏳{m['ex']}" if m["ex"] else ""
            if m["grade"]:
                note = f"<br>{GRADE_LABEL.get(m['grade'], m['grade'])}"
            elif m["gu"]:
                note = "<br>等级待定"
            else:
                note = ""
            head = f"![{name}]({img})" if img else "·"
            cells.append(f"{head}<br>**{name}**<br>{qty}{ex}{note}")
        for i in range(0, len(cells), cols):
            band = cells[i:i + cols]
            band += [""] * (cols - len(band))
            out.append("| " + " | ".join(band) + " |")
            if i == 0:
                out.append("|" + ":--:|" * cols)
        out.append("")

    if pend:
        out.append("## 名字待定")
        out.append("")
        # 待定格子也按「图鉴分类」归组（图鉴没收录的才留在它出现的页）：
        # 家具蓝图图鉴是票券，即使是在物料页认到的，图也该跟到票券下面去。
        regroup: dict[str, list[dict]] = {}
        for page, cells_ in pend.items():
            for c in cells_:
                nm = aliases.get(norm_name(c.get("name") or ""),
                                 norm_name(c.get("name") or ""))
                cx = codex.get(nm) or codex.get(nm.casefold()) if nm else None
                regroup.setdefault(CAT_OVERRIDE.get(nm) or (cx or {}).get("cat") or page,
                                   []).append(c)
        for cat in [c for c in CATS if c in regroup] + [c for c in regroup if c not in CATS]:
            cells_ = regroup[cat]
            out.append(f"### {cat}")
            out.append("")
            cards = []
            for c in sorted(cells_, key=lambda x: (x["row"], x["col"])):
                guess = aliases.get(norm_name(c.get("name") or ""),
                                    norm_name(c.get("name") or "")) or "?"
                qty = c.get("count_final") or "?"
                ex = f" ⏳{c['expire_text']}" if c.get("expire_text") else ""
                # 名字存疑不代表等级也存疑：外框色是独立量出来的，照样写上去。
                grade = c.get("grade") or ""
                note = f"<br>{GRADE_LABEL.get(grade, grade)}" if grade else ""
                # 名字把握够（score ≥ 0.60）且图标库里有这张图，就用正经物品图；
                # 只有名字本身没定下来时才退回去裁截图 —— 拿可能错的图标贴上去等于二次误导。
                img = None
                if (c.get("score") or 0) >= 0.60:
                    icon_path, _ = pick_icon(guess, icons)
                    if icon_path:
                        img = thumb_file(icon_path, guess)
                img = img or crop_thumb(c)
                head = f"![{guess}]({img})" if img else "·"
                cards.append(f"{head}<br>⚠ {guess}<br>{qty}{ex}{note}")
            for i in range(0, len(cards), cols):
                band = cards[i:i + cols]
                band += [""] * (cols - len(band))
                out.append("| " + " | ".join(band) + " |")
                if i == 0:
                    out.append("|" + ":--:|" * cols)
            out.append("")
    return "\n".join(out) + "\n"



# ---------- 采集 ----------

def load(cats: tuple[str, ...]) -> list[dict]:
    docs = []
    for cat in cats:
        p = WH_DIR / f"{cat}.json"
        if not p.exists():
            print(f"[跳过] 没有 {p}")
            continue
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["category"] = doc.get("category") or cat
        docs.append(doc)
    return docs


def build_items(doc: dict, icons: dict[str, Path],
                catalog: dict[str, dict] | None = None,
                aliases: dict[str, str] | None = None) -> list[dict]:
    catalog = catalog or {}
    aliases = aliases or {}
    # 大小写兜底：OCR 常把「核定牌V」读成小写 v
    fold_idx: dict[str, dict] = {}
    for k, v in catalog.items():
        fold_idx.setdefault(k.casefold(), v)
    items = []
    for c in doc["cells"]:
        if c.get("status") == "empty":
            continue
        name = norm_name(c.get("name") or "")
        flags = []
        # OCR 读错的名字先按别名表纠正（名卡→改名卡、征然信→征集信…）
        fixed = aliases.get(norm_name(name)) if name else None
        if fixed:
            flags.append(f"原名 {name}")
            name = fixed
        qty_txt = c.get("count_final") or ""
        qty = parse_qty(qty_txt)
        if not name:
            flags.append("无名")
        # 图鉴：名字命中就用图鉴标准名，并带上品质/用途/描述
        cat = catalog.get(norm_name(name)) if name else None
        if cat is None and name:
            cat = fold_idx.get(norm_name(name).casefold())
        if cat:
            if cat.get("name") and cat["name"] != name:
                flags.append(f"图鉴名 {cat['name']}")
                name = cat["name"]
        elif name:
            flags.append("图鉴未收录")
        path, how = pick_icon(name, icons)
        if path is None:
            flags.append("无图")
        if c.get("name_source") == "icon":
            flags.append("图标识别")
        if c.get("status") == "uncertain":
            flags.append("名称存疑")
        if not fixed and (c.get("sim") or 0) and c["sim"] < 0.75:
            flags.append(f"名称存疑 {c['sim']}")
        if c.get("count_conflict"):
            flags.append(f"数量口径 {c.get('stock')}≠{c.get('count_px')}")
        items.append({
            "name": name or "(未识别)",
            "qty": qty,
            "qty_txt": qty_txt,
            "expire": c.get("expire_text") or "",
            "expire_days": c.get("expire_days"),
            "grade_unknown": bool(c.get("grade_unknown")),
            "colors": c.get("grade_unknown") or [],
            "img": thumb_data_uri(path, name),
            "color": placeholder_color(name),
            "flags": flags,
            "quality": (cat or {}).get("quality", ""),
            "usage": (cat or {}).get("usage", ""),
            "desc": (cat or {}).get("desc", ""),
            "in_catalog": bool(cat),
            "row": c.get("row"), "col": c.get("col"),
            "source": c.get("name_source") or "",
        })
    return items


def aggregate(items: list[dict], codex: dict[str, dict] | None = None) -> list[dict]:
    codex = codex if codex is not None else codex_index()
    agg: dict[str, dict] = {}
    for it in items:
        # 「同一张图、不同等级」的物品（考核自选包 I~V…）等级定不了，但数量可以相加；
        # 合成一张卡并标注「等级待定」，比拆成四五张一模一样的卡更好读。
        k = it["name"]
        a = agg.setdefault(k, {"name": it["name"], "qty": 0.0, "cells": 0,
                               "img": it["img"], "color": it["color"],
                               "quality": it["quality"], "usage": it["usage"],
                               "in_catalog": it["in_catalog"],
                               "grade_unknown": it.get("grade_unknown", False),
                               "flags": set(), "expire": []})
        a["qty"] += it["qty"]
        a["cells"] += 1
        a["flags"].update(f for f in it["flags"] if f not in ("无图",))
        if a.get("grade_unknown"):
            a["flags"].add("等级待定")
        if it.get("expire"):
            a["expire"].append(it["expire"])
    # 排序按图鉴里的物品 ID（用户 2026-11-22 要求「排版按物品id来」）；
    # 图鉴没有的（改名卡）以及没有名字的排到最后。
    def _key(a: dict) -> tuple:
        cx = codex.get(a["name"]) or codex.get(a["name"].casefold()) or {}
        iid = cx.get("id")
        return (iid if isinstance(iid, int) else 10 ** 9, a["name"])

    out = sorted(agg.values(), key=_key)
    for a in out:
        a["flags"] = sorted(a["flags"])
        if a["expire"]:
            # 同名多格到期日通常不同（分批到货），全列出来而不是取最小
            a["flags"].append("到期 " + "/".join(a["expire"]))
    return out


# ---------- 命令行 ----------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", nargs="*", default=list(CATS))
    ap.add_argument("--out", default=str(OUT_DIR / "仓库快照.md"))
    args = ap.parse_args(argv[1:])

    docs = load(tuple(args.cat))
    if not docs:
        print("没有可用的仓库数据")
        return 1
    icons = icon_index()
    catalog = catalog_index()
    aliases = alias_index()
    print(f"[图鉴] {len(catalog)} 条名称 / 图标 {len(icons)} 张 / 别名 {len(aliases)} 条")
    md = render_md_grid(docs, icons, catalog, aliases)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"[写出] {out}  {out.stat().st_size:,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
