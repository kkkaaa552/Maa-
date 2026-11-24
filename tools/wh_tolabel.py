"""列出待人工标注的格子，并生成带序号的对照图。"""
import argparse, json, pathlib, sys
import cv2, numpy as np
sys.path.insert(0, r"D:\mixian\my-maa\tools")
import wh_dhash
from PIL import Image, ImageDraw, ImageFont

X0, Y0, PX, PY = 365, 130, 150, 128
FONT = r"C:\Windows\Fonts\msyh.ttc"


def load(p):
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", required=True)
    ap.add_argument("--shot", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    data = json.loads(pathlib.Path(f"data/warehouse/{a.cat}.json").read_text(encoding="utf-8"))
    cells = [c for c in data["cells"] if c.get("status") == "panel"]
    shot = load(a.shot)

    todo = []
    for i, c in enumerate(cells):
        x, y = X0 + c["col"] * PX, Y0 + c["row"] * PY
        icon = shot[y + 8:y + 8 + 84, x + 8:x + 8 + 84]
        h = wh_dhash.dhash(icon)
        ov = wh_dhash.lookup(h)
        need = (not ov) and (not c.get("name") or c.get("name_source") == "icon")
        todo.append((i, c, h, icon, need))

    print(f"{a.cat}: 共 {len(cells)} 格，待标注 {sum(1 for t in todo if t[4])} 格")
    for i, c, h, _, need in todo:
        if need:
            print(f"  #{i:<3} r{c['row']}c{c['col']}  数量={c.get('count_px')}  hash={h}  猜名={c.get('name') or ''}({c.get('name_source')})")

    cols = 6
    sel = [t for t in todo if t[4]]
    rows = max(1, (len(sel) + cols - 1) // cols)
    W, H = cols * 160 + 20, rows * 200 + 70
    canvas = Image.new("RGB", (W, H), (20, 22, 26))
    dr = ImageDraw.Draw(canvas)
    dr.text((16, 16), f"{a.cat} 待标注 {len(sel)} 项 —— 告诉我「#序号 = 名称」即可", font=ImageFont.truetype(FONT, 22), fill=(240, 236, 226))
    for k, (i, c, h, icon, need) in enumerate(sel):
        r, col = divmod(k, cols)
        cx, cy = 16 + col * 160, 62 + r * 200
        img = cv2.cvtColor(cv2.resize(icon, (84, 84), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        canvas.paste(Image.fromarray(img), (cx + 30, cy))
        dr.text((cx + 26, cy + 92), f"#{i}", font=ImageFont.truetype(FONT, 18), fill=(255, 190, 90))
        dr.text((cx + 26, cy + 116), str(c.get("count_px") or ""), font=ImageFont.truetype(FONT, 16), fill=(150, 200, 160))
        dr.text((cx + 26, cy + 140), h[:8], font=ImageFont.truetype(FONT, 13), fill=(120, 124, 130))
    canvas.save(a.out)
    print("对照图:", a.out)


if __name__ == "__main__":
    main()
