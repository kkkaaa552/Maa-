"""图标指纹 + 名称覆盖表。"""
import json, pathlib
import cv2, numpy as np

ROOT = pathlib.Path(r"D:\mixian\my-maa")
OVR = ROOT / "data/warehouse/name_override.json"


def dhash(img_bgr, size=8):
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    val = 0
    for b in bits:
        val = (val << 1) | int(b)
    return f"{val:016x}"


def load_ovr():
    if OVR.exists():
        return json.loads(OVR.read_text(encoding="utf-8"))
    return {"version": 1, "items": {}}


def save_ovr(d):
    OVR.parent.mkdir(parents=True, exist_ok=True)
    OVR.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def lookup(h):
    return load_ovr().get("items", {}).get(h)


def excluded(h):
    return h in load_ovr().get("exclude", [])
