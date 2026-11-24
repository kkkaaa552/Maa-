"""把一次仓库扫描结果渲染成图鉴样式的大图（图标 + 名称 + 数量）。"""
import argparse, json, pathlib
import cv2, numpy as np
from PIL import Image, ImageDraw, ImageFont
import wh_dhash

X0, Y0, PX, PY, CW, CH = 365, 130, 150, 128, 100, 100
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"


def load(p):
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", required=True)
    ap.add_argument("--shot", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, default=5)
    a = ap.parse_args()

    data = json.loads(pathlib.Path(f"data/warehouse/{a.cat}.json").read_text(encoding="utf-8"))
    cells = [c for c in data["cells"] if c.get("status") == "panel"]
    shot0 = load(a.shot)
    def _h(c):
        x, y = X0 + c["col"] * PX, Y0 + c["row"] * PY
        return wh_dhash.dhash(shot0[y + 8:y + 8 + 84, x + 8:x + 8 + 84])
    dropped = [c for c in cells if wh_dhash.excluded(_h(c))]
    cells = [c for c in cells if not wh_dhash.excluded(_h(c))]
    shot = load(a.shot)

    CW2, CH2 = 150, 190            # 每格画布
    cols = a.cols
    rows = (len(cells) + cols - 1) // cols
    W, H = cols * CW2 + 40, rows * CH2 + 90
    canvas = Image.new("RGB", (W, H), (18, 20, 24))
    dr = ImageDraw.Draw(canvas)
    f_title = ImageFont.truetype(FONT_B, 26)
    f_name = ImageFont.truetype(FONT, 17)
    f_num = ImageFont.truetype(FONT_B, 17)
    dr.text((20, 18), f"{a.cat}　共 {len(cells)} 项" + (f"（已排除 {len(dropped)} 项限定/不统计物品）" if dropped else ""), font=f_title, fill=(235, 232, 225))

    for i, c in enumerate(cells):
        r, col = divmod(i, cols)
        cx, cy = 20 + col * CW2, 70 + r * CH2
        x, y = X0 + c["col"] * PX, Y0 + c["row"] * PY
        icon = shot[y + 8:y + 8 + 84, x + 8:x + 8 + 84]
        icon = cv2.resize(icon, (84, 84), interpolation=cv2.INTER_AREA)
        icon = cv2.cvtColor(icon, cv2.COLOR_BGR2RGB)
        canvas.paste(Image.fromarray(icon), (cx + 25, cy))
        icon_bgr = shot[y + 8:y + 8 + 84, x + 8:x + 8 + 84]
        h = wh_dhash.dhash(icon_bgr)
        ov = wh_dhash.lookup(h)
        src = "override" if ov else c.get("name_source", "")
        name = ov or (c.get("name") if c.get("name_source") != "icon" else "") or "?"
        fn = f_name if len(name) <= 8 else ImageFont.truetype(FONT, 15 if len(name) <= 10 else 13)
        if len(name) > 11:
            name = name[:11] + "…"
        dr.text((cx + 14, cy + 92), name, font=fn, fill=(226, 224, 216))
        dr.text((cx + 22, cy + 116), str(c.get("count_px") or c.get("count_final") or "?"),
                font=f_num, fill=(255, 214, 120))
        if name == "?":
            dr.text((cx + 22, cy + 140), f"待标注 #{i}", font=ImageFont.truetype(FONT, 13), fill=(210, 120, 120))

    canvas.save(a.out)
    print("写出", a.out, canvas.size)


if __name__ == "__main__":
    main()
