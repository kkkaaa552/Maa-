#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py — 把项目打成一个可分发的发布包（替代官方模板的 tools/install.py）。

官方 MaaPracticeBoilerplate 的 install.py 假设这样的布局：

    assets/resource/        ->  install/resource/
    assets/interface.json   ->  install/interface.json
    agent/                  ->  install/agent/
    deps/bin/               ->  install/runtimes/<tag>/native/

本项目是扁平布局，且通用 UI 与原生库都已就位，所以对应关系变成：

    resource/               ->  install/resource/
    interface.json          ->  install/interface.json   （同时改 child_exec + 写 version）
    agent/  tools/  data/   ->  install/ 同名目录
    runtimes/ libs/ plugins/->  install/ 同名目录（UI 运行库，直接搬）
    python/                 ->  install/python/（嵌入式 Python + 依赖，agent 靠它跑）

用法：

    python tools/build.py <version> [os] [arch]
    python tools/build.py 0.1.0
    python tools/build.py 0.1.0 win x86_64 --no-python   # 不带 Python 运行时
    python tools/build.py 0.1.0 --no-zip                 # 只拼 install/，不压缩

产物：

    install/                          拼装好的目录（可直接运行）
    dist/<包名>-<tag>-<os>-<arch>.zip  压缩包（除非 --no-zip）

设计上的几个取舍（写在这里避免以后自己被绊一下）：

  * 不搬 maafw/（88.6 MB）—— 它是 main.py 手工调试模式用的 5.16.0-beta.2 原生库，
    通用 UI 走 runtimes/（5.14.3），agent 走 python/Lib/site-packages/maa/bin（5.16.1）。
    三条链路本来就各用各的，发布包里不需要第三条。
  * 不搬 .venv/ backup/ debug/ logs/ report/ _dl/ temp/ —— 见 EXCLUDE_TOP。
  * data/warehouse/*.json 不搬（那是上一次扫描结果），但 name_override.json 要搬。
  * config/instances/ 不搬（设备配置属于用户本机），只带 config.json + maa_option.json 作默认值。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "install"
DIST_DIR = ROOT / "dist"

# 包名取 interface.json 的 name 字段；发布 zip 用它当前缀
DEFAULT_PKG = "物华弥新MAA"

# 顶层：这些目录/文件一律不进包
EXCLUDE_TOP = {
    ".venv", ".git", ".github", "backup", "debug", "logs", "report", "dist",
    "_dl", "temp", "__pycache__", "install", "maafw", "_buildtest",
    "config",  # 单独按白名单处理
    "data",    # 单独按白名单处理
}

# 顶层：这些文件不进包（备份件、临时截图、待做清单等开发产物）
EXCLUDE_FILES = (
    ".bak", ".bak-", ".preicon-", ".orig-", ".before-",
)
EXCLUDE_NAMES = {
    "mastate.json",
    "00-待做清单-仓库识别.md",
    "HANDOFF.md", "HANDOFF-交接.md", "HANDOFF-均衡材料刷取.md",
}

# config 里要带进包的文件（相对 config/）
CONFIG_KEEP = ["config.json", "maa_option.json"]
# data 里要带进包的文件（相对 data/）
DATA_KEEP_DIRS = [
    "digits",     # 数字字模，缺了 wh_counts.build 直接崩
    "refbook",    # 三份图鉴 xlsx + icons，4.8 MB；带上用户才能自己重跑 build_whitelist.py
]
DATA_KEEP_FILES = [
    "catalog.json", "codex.json", "name_alias.json", "whitelist.json",
    "warehouse/name_override.json",
]

# ---- exe 改名的映射：官方 apphost -> 项目名 ----
APP_EXE_SRC = "MFAAvalonia.exe"
APP_EXE_DST = "物华弥新MAA.exe"
ICON_SRC = "resource/image/logo.png"


# ============================ 工具函数 ============================

def log(msg: str) -> None:
    print(msg, flush=True)


def copytree(src: Path, dst: Path, skip_names: set[str] | None = None) -> tuple[int, int]:
    """拷贝目录，返回 (文件数, 字节数)。跳过 __pycache__ 与给定文件名。"""
    n = b = 0
    skip_names = skip_names or set()
    for cur, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = Path(cur).relative_to(src)
        target = dst / rel
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            if f in skip_names or any(s in f for s in EXCLUDE_FILES):
                continue
            s = Path(cur) / f
            d = target / f
            shutil.copy2(s, d)
            n += 1
            b += s.stat().st_size
    return n, b


def copy_file(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return src.stat().st_size


def size_of(p: Path) -> tuple[int, int]:
    n = b = 0
    for cur, dirs, files in os.walk(p):
        for f in files:
            n += 1
            b += (Path(cur) / f).stat().st_size
    return n, b


# ============================ 各步骤 ============================

def step_ui() -> None:
    """通用 UI 运行库：libs / runtimes / plugins + 根目录几个 dll。"""
    log("[1/8] 通用 UI 运行库")
    total_n = total_b = 0
    for d in ("libs", "runtimes", "plugins"):
        src = ROOT / d
        if not src.is_dir():
            log(f"  ! 缺目录 {d}，跳过")
            continue
        n, b = copytree(src, BUILD_DIR / d)
        total_n += n
        total_b += b
        log(f"  {d:<10} {n:>5} 文件  {b / 1024 / 1024:>8.1f} MB")
    for f in ("MFAAvalonia.dll", "MFAAvalonia.deps.json", "MFAAvalonia.runtimeconfig.json", "libloader.dll"):
        src = ROOT / f
        if src.is_file():
            total_b += copy_file(src, BUILD_DIR / f)
            total_n += 1
            log(f"  {f:<10} 已拷")
        else:
            log(f"  ! 缺 {f}")
    log(f"  小计 {total_n} 文件 {total_b / 1024 / 1024:.1f} MB")


def step_exe_and_icon() -> None:
    """把官方 apphost 改名成项目名，并写入 logo 图标。"""
    log("[2/8] 主程序与图标")
    src = ROOT / APP_EXE_SRC
    if not src.is_file():
        # 已经改过名（本地开发目录就是这个状态）
        alt = ROOT / APP_EXE_DST
        if alt.is_file():
            copy_file(alt, BUILD_DIR / APP_EXE_DST)
            log(f"  {APP_EXE_DST} 直接拷（已是改名后的版本）")
            return
        log(f"  ! {APP_EXE_SRC} 与 {APP_EXE_DST} 都不存在，跳过")
        return

    dst = BUILD_DIR / APP_EXE_DST
    copy_file(src, dst)
    log(f"  {APP_EXE_SRC} -> {APP_EXE_DST}  {src.stat().st_size:,} B")

    icon = ROOT / ICON_SRC
    if not icon.is_file():
        log(f"  ! 缺图标 {ICON_SRC}，保持默认图标")
        return
    try:
        n = set_exe_icon(dst, icon)
        log(f"  图标已写入：{n} 个尺寸，exe 现在 {dst.stat().st_size:,} B")
    except Exception as e:  # 图标失败不该让整包作废
        log(f"  ! 写图标失败（不影响运行）：{type(e).__name__}: {e}")


def step_python() -> None:
    """嵌入式 Python + site-packages。"""
    log("[3/8] Python 运行时")
    src = ROOT / "python"
    if not src.is_dir():
        log("  ! 缺 python/ 目录 —— 包里不会有 Python，用户必须自己装")
        log("    （CI 里由 build.py --fetch-python 或工作流提前准备）")
        return
    n, b = copytree(src, BUILD_DIR / "python")
    log(f"  python/    {n:>5} 文件  {b / 1024 / 1024:>8.1f} MB")


def step_resource() -> None:
    log("[4/8] 资源包")
    n, b = copytree(ROOT / "resource", BUILD_DIR / "resource")
    log(f"  resource/  {n:>5} 文件  {b / 1024 / 1024:>8.1f} MB")


def step_code() -> None:
    log("[5/8] 代码与数据")
    for d in ("agent", "tools", "data", "vendor"):
        src = ROOT / d
        if not src.is_dir():
            log(f"  ! 缺 {d}/")
            continue
        if d == "data":
            n, b = copy_data()
        else:
            n, b = copytree(src, BUILD_DIR / d)
        log(f"  {d:<10} {n:>5} 文件  {b / 1024 / 1024:>8.1f} MB")
    # main.py / maaenv.py 是手工调试入口；README / LICENSE 对应官方 install.py 的
    # install_chores() —— 少这两个，用户拿到包不知道该装什么运行时、按什么协议分发。
    for f in ("main.py", "maaenv.py", "README.md", "LICENSE"):
        src = ROOT / f
        if src.is_file():
            copy_file(src, BUILD_DIR / f)
            log(f"  {f} 已拷")
        else:
            log(f"  ! 缺 {f}（不影响运行，但包里会少一份说明/许可）")


def copy_data() -> tuple[int, int]:
    """data/ 只带真正需要的：字模 + 白名单 + 图鉴 + 上一次结果的名字覆盖表。"""
    n = b = 0
    for d in DATA_KEEP_DIRS:
        src = ROOT / "data" / d
        if src.is_dir():
            nn, bb = copytree(src, BUILD_DIR / "data" / d)
            n += nn
            b += bb
    for f in DATA_KEEP_FILES:
        src = ROOT / "data" / f
        if src.is_file():
            b += copy_file(src, BUILD_DIR / "data" / f)
            n += 1
        else:
            log(f"  ! 缺 data/{f}")
    return n, b


def step_config() -> None:
    log("[6/8] 默认配置")
    n = 0
    for f in CONFIG_KEEP:
        src = ROOT / "config" / f
        if src.is_file():
            copy_file(src, BUILD_DIR / "config" / f)
            n += 1
            log(f"  config/{f}")
        else:
            log(f"  ! 缺 config/{f}")
    if n == 0:
        (BUILD_DIR / "config").mkdir(parents=True, exist_ok=True)
        log("  （无默认配置，留空目录）")


def step_interface(version: str) -> None:
    """拷 interface.json，改 child_exec，写 version。"""
    log("[7/8] interface.json")
    src = ROOT / "interface.json"
    data = json.loads(src.read_text(encoding="utf-8"))

    has_python = (BUILD_DIR / "python" / "python.exe").is_file()
    old_exec = data.get("agent", {}).get("child_exec", "")
    if has_python:
        data["agent"]["child_exec"] = "./python/python.exe"
        log(f"  child_exec: {old_exec}  ->  ./python/python.exe")
    else:
        log(f"  ! 包里没有 python/，child_exec 保持 {old_exec}（用户需自装 Python 3.13）")

    data["version"] = version
    log(f"  version: -> {version}")

    out = BUILD_DIR / "interface.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"  写出 {out.stat().st_size:,} B")


def step_zip(name: str, tag: str, os_name: str, arch: str) -> Path:
    log("[8/8] 压缩")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    out = DIST_DIR / f"{name}-{tag}-{os_name}-{arch}.zip"
    if out.exists():
        out.unlink()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for cur, dirs, files in os.walk(BUILD_DIR):
            for f in files:
                p = Path(cur) / f
                # 相对 BUILD_DIR 写，解压出来直接是 interface.json / libs/ ... 而不是多一层 install/
                zf.write(p, p.relative_to(BUILD_DIR))
                n += 1
    log(f"  {out.name}")
    log(f"  {n} 文件, 压缩后 {out.stat().st_size / 1024 / 1024:.1f} MB")
    return out


# ============================ 图标写入 ============================

def set_exe_icon(exe: Path, png: Path) -> int:
    """把 png 转成多尺寸 ICO 并写进 exe 的 RT_ICON / RT_GROUP_ICON。

    只用标准库 + ctypes（CI 上没有 Pillow 也能跑之前的那份脚本要 Pillow，
    这里保留 Pillow，因为它本来就在 requirements 里）。
    """
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    sizes = [16, 24, 32, 48, 64, 96, 128, 256]
    im = Image.open(png).convert("RGBA")
    frames = [im.resize((s, s), Image.LANCZOS) for s in sizes]

    # 组一张 ICO 的字节流
    import io

    buf = io.BytesIO()
    frames[-1].save(buf, format="ICO", sizes=[(s, s) for s in sizes])
    ico_bytes = buf.getvalue()

    # 解析 ICO 目录，取出每个 PNG 块
    (reserved, itype, count) = struct.unpack_from("<HHH", ico_bytes, 0)
    if reserved != 0 or itype != 1:
        raise ValueError("不是合法的 ICO")
    entries = []
    for i in range(count):
        off = 6 + i * 16
        w, h, ncol, rsv, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", ico_bytes, off)
        entries.append((w, h, ncol, planes, bpp, ico_bytes[offset:offset + size]))

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.BeginUpdateResourceW.restype = wintypes.HANDLE
    k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    k32.UpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID,
                                    wintypes.WORD, wintypes.LPVOID, wintypes.DWORD]
    k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]

    h = k32.BeginUpdateResourceW(str(exe), False)
    if not h:
        raise OSError(f"BeginUpdateResourceW 失败: {ctypes.get_last_error()}")

    try:
        # RT_ICON = 3，资源 id 从 1 开始
        for idx, (w, h_, ncol, planes, bpp, blob) in enumerate(entries, start=1):
            ok = k32.UpdateResourceW(h, ctypes.cast(3, wintypes.LPVOID),
                                     ctypes.cast(idx, wintypes.LPVOID), 0,
                                     blob, len(blob))
            if not ok:
                raise OSError(f"UpdateResourceW(RT_ICON {idx}) 失败")

        # RT_GROUP_ICON = 14，id = 32512（与 Explorer 默认图标一致）
        # 结构：GRPICONDIR (reserved, type=1, count) + N * GRPICONDIRENTRY (14 B)
        grp = struct.pack("<HHH", 0, 1, len(entries))
        for idx, (w, h_, ncol, planes, bpp, blob) in enumerate(entries, start=1):
            grp += struct.pack("<BBBBHHIH", w, h_, ncol, 0, planes or 1, bpp or 32, len(blob), idx)
        ok = k32.UpdateResourceW(h, ctypes.cast(14, wintypes.LPVOID),
                                 ctypes.cast(32512, wintypes.LPVOID), 0,
                                 grp, len(grp))
        if not ok:
            raise OSError("UpdateResourceW(RT_GROUP_ICON) 失败")
    finally:
        k32.EndUpdateResourceW(h, True)

    return len(entries)


# ============================ main ============================

def main() -> int:
    ap = argparse.ArgumentParser(description="打包物华弥新MAA 发布包")
    ap.add_argument("version", help="版本号，例如 0.1.0")
    ap.add_argument("os", nargs="?", default="win", help="win / macos / linux")
    ap.add_argument("arch", nargs="?", default="x86_64")
    ap.add_argument("--no-zip", action="store_true", help="只拼 install/，不压缩")
    ap.add_argument("--no-python", action="store_true", help="不打包 python/ 目录")
    ap.add_argument("--keep", action="store_true", help="保留旧的 install/ 内容（默认先清空）")
    args = ap.parse_args()

    tag = args.version if args.version.startswith("v") else f"v{args.version}"

    log(f"=== 打包 {DEFAULT_PKG} {tag} {args.os}-{args.arch} ===")
    log(f"源目录: {ROOT}")

    if BUILD_DIR.exists() and not args.keep:
        shutil.rmtree(BUILD_DIR)
        log("已清空旧的 install/")
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # 前置校验
    if not (ROOT / "interface.json").is_file():
        log("! interface.json 不存在，中止")
        return 1
    if not (ROOT / "resource").is_dir():
        log("! resource/ 不存在，中止")
        return 1

    step_ui()
    step_exe_and_icon()
    if args.no_python:
        log("[3/8] Python 运行时 —— 按 --no-python 跳过")
    else:
        step_python()
    step_resource()
    step_code()
    step_config()
    step_interface(args.version)

    n, b = size_of(BUILD_DIR)
    log("")
    log(f"=== install/ 合计 {n} 文件, {b / 1024 / 1024:.1f} MB ===")

    if not args.no_zip:
        out = step_zip(DEFAULT_PKG, tag, args.os, args.arch)
        log(f"\n产物: {out}")
    else:
        log(f"\n产物目录: {BUILD_DIR}")

    # 自检：关键文件是否都在
    must = [
        "interface.json", "resource/pipeline", "resource/model/ocr/det.onnx",
        "agent/warehouse_agent.py", "tools/wh_scan.py", "tools/wh_counts.py",
        "data/digits/d0.png", "data/whitelist.json",
        "libs", "runtimes", APP_EXE_DST,
        "README.md", "LICENSE",
    ]
    log("\n=== 自检 ===")
    bad = 0
    for m in must:
        p = BUILD_DIR / m
        ok = p.exists()
        if not ok:
            bad += 1
        log(f"  {'OK ' if ok else 'MISS'} {m}")

    if (BUILD_DIR / "python" / "python.exe").is_file():
        py = BUILD_DIR / "python" / "python.exe"
        # 自检必须模拟「最终用户」的环境：包里没有 maafw/（它只是开发目录 main.py
        # 手工模式的第三条原生库链路），所以这里要**清掉** MAAFW_BINARY_PATH，
        # 让 maa 用它自带的 python/Lib/site-packages/maa/bin。
        # 坑（2026-11-24 实测）：早先这里写的是 MAAFW_BINARY_PATH=str(ROOT/"maafw")，
        # 而 maafw/ 既不进发布包也不进 git（.gitignore:49），CI checkout 后必定
        # 抛 FileNotFoundError: `<install>/maafw` does not exist，导致整包判失败。
        env = {k: v for k, v in os.environ.items() if k != "MAAFW_BINARY_PATH"}
        r = subprocess.run(
            [str(py), "-c", "import numpy,cv2,PIL,openpyxl,jsonschema,maa;print('ok')"],
            capture_output=True, text=True, env=env,
        )
        ok = r.returncode == 0 and "ok" in r.stdout
        log(f"  {'OK ' if ok else 'FAIL'} python 依赖 import 自检")
        if not ok:
            log(f"       stdout={r.stdout.strip()[:200]}")
            log(f"       stderr={r.stderr.strip()[:300]}")
            bad += 1
    else:
        log("  --  python/ 未打包，跳过依赖自检")

    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
