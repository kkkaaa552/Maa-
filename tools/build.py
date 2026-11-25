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

  * 不搬 maafw/（88.6 MB）—— 它是 main.py 手工调试模式用的原生库，发布包里不需要。
  * **MaaFramework 只带一套**：以前 runtimes/ 是 5.14.3（界面用）、
    python/Lib/site-packages/maa/bin 是 5.16.1（agent 用），两套同名 dll 各 86 MB。
    step_maa_single_source() 会把 maa/bin 那套 5.16.1 覆盖进 runtimes/native，
    再删掉 maa/bin 里的 dll；agent 靠 warehouse_agent.py 自设
    MAAFW_BINARY_PATH 指向 runtimes/native 来加载。省约 86 MB（压缩后约 35 MB）。
  * 不搬 .venv/ backup/ debug/ logs/ report/ _dl/ temp/ —— 见 EXCLUDE_TOP。
  * data/warehouse/*.json 不搬（那是上一次扫描结果），但 name_override.json 要搬。
  * config/instances/ 不搬（设备配置属于用户本机）；默认值写成
    config/config.template.json 而不是 config.json —— MFAAvalonia 的约定是
    「目录里没有其它配置时才把 template 转成 config.json」，写成 config.json
    会让用户下次解压覆盖时装过的语言/主题/布局被直接盖掉。
"""

from __future__ import annotations

import argparse
import hashlib
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
    ".bak", ".bak-", ".preicon-", ".orig-", ".before-", ".new",
)
EXCLUDE_NAMES = {
    "mastate.json",
    "00-待做清单-仓库识别.md",
    "HANDOFF.md", "HANDOFF-交接.md", "HANDOFF-均衡材料刷取.md",
}

# 发布包里 config/config.json 的内容。**不要**直接拷开发机的 config/config.json：
# 那个文件里有 UI.MainWindow.PositionX = -32000（开发机最小化状态留下的）、
# Width 1440 / Height 781、UI.HasCompletedFirstUseTutorial = true，以及一份
# UI.DashboardCardGrid.ResourceLayoutHash。用户装上打开就是「教程早就看过、
# 窗口尺寸被钉死」，观感上像是二手货。
# 这里只留与机器无关的 UI 偏好，其余键交给 MFAAvalonia 首次启动自己生成。
CLEAN_CONFIG = {
    "CurrentLanguage": "zh-CN",
    "ColorTheme": "Blue",
    "BaseTheme": "Light",
    "EnableEdit": False,
}

# 拷贝之后被 build.py 有意改写过内容的文件（相对 BUILD_DIR），内容对账时跳过
CONTENT_MUTATED = {
    "interface.json",
    "config/config.template.json",
}

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
            _note(d.relative_to(BUILD_DIR), s)
            n += 1
            b += s.stat().st_size
    return n, b


def copy_file(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    _note(dst.relative_to(BUILD_DIR), src)
    return src.stat().st_size


def size_of(p: Path) -> tuple[int, int]:
    n = b = 0
    for cur, dirs, files in os.walk(p):
        for f in files:
            n += 1
            b += (Path(cur) / f).stat().st_size
    return n, b


# ============================ 自检 / 洁净 ============================

_bad = 0

# 拷贝清单：相对 BUILD_DIR 的 posix 路径 -> 源文件。copy_file()/copytree() 自动登记。
# 内容对账只认这份清单——BUILD_DIR 里可能还有 exe 自己生成的 appsettings.json、
# --keep 留下的旧文件，它们没有源，不该拿来跟源比。
_copied: dict[str, Path] = {}


def fail(msg: str) -> None:
    """记一条致命问题。main() 在压缩之前汇总，只要计数非 0 就不产出 zip。"""
    global _bad
    _bad += 1
    log(f"  FAIL {msg}")


def _note(rel: Path, src: Path) -> None:
    _copied[rel.as_posix()] = src


def _forget(rel: str) -> None:
    """把某个文件移出对账清单：它的内容会被 build.py 有意改写，跟源比没有意义。"""
    _copied.pop(rel, None)


def _sha(p: Path) -> str:
    """分块算 sha256。大文件别一次 read()，DirectML.dll 有 18 MB。"""
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ≥ 这个体积的文件做 sha256 对账；小文件只比体积（省时间，且小文件损坏概率极低）
HASH_THRESHOLD = 1 << 20


def check_content() -> tuple[int, int]:
    """对账拷贝后的内容，返回 (检查文件数, 哈希文件数)。

    为什么需要这一步（2026-11-24 实测）：build.py 原来的校验全是「文件在不在」。
    有一次 --keep 构建把 runtimes/win-x64/native/DirectML.dll 拷成了坏文件——
    长度一模一样（18,527,776 B），前 64 字节 MZ 头也一样，但 46.4% 的字节不同、
    82.2% 是零。所有体积类检查都放过，产物里 exe 一启动就 0xC0000005
    （事件日志 BEX64 / Fault offset 0xa41428 / Faulting module unknown）。
    受控实验：只把该文件从 0x6a83bc 起置零，sha256 恰好变成坏件那个值，
    启动立刻复现同一个退出码。→ 只比体积不够，关键件必须比内容。
    """
    n = nh = 0
    for rel, src in sorted(_copied.items()):
        if rel in CONTENT_MUTATED:
            continue  # build.py 自己改过内容，跟源比必然不等
        d = BUILD_DIR / rel
        if not d.is_file():
            fail(f"拷贝后丢失: {rel}")
            continue
        n += 1
        ss, ds = src.stat().st_size, d.stat().st_size
        if ss != ds:
            fail(f"拷贝后体积不符: {rel}（源 {ss} / 包 {ds}）")
            continue
        if ss >= HASH_THRESHOLD:
            nh += 1
            if _sha(src) != _sha(d):
                fail(f"拷贝后内容损坏: {rel}（体积相同但 sha256 不同）")
    return n, nh


def purge_pyc(root: Path) -> tuple[int, int]:
    """删掉 root 下所有 .pyc/.pyo 与空的 __pycache__ 目录，返回 (文件数, 目录数)。

    为什么非要有这一步：嵌入式 Python 每被 import 一次就在 site-packages 里写
    __pycache__。而 build.py 自己的依赖自检跑的就是 install/python/python.exe，
    自检完 install/ 里就凭空多出几百个 pyc。copytree 只跳「源目录里」的
    __pycache__（见 copytree() 的 dirs[:] 过滤），管不到「目标目录里」后生成的，
    所以必须在压缩前单独扫一遍。2026-11-24 那版产物里 325 个 pyc 就是这么进去的。
    """
    nf = nd = 0
    for cur, dirs, files in os.walk(root, topdown=False):
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                try:
                    (Path(cur) / f).unlink()
                    nf += 1
                except OSError:
                    pass
        d = Path(cur)
        if d.name == "__pycache__" and d != root:
            try:
                if not any(d.iterdir()):
                    d.rmdir()
                    nd += 1
            except OSError:
                pass
    return nf, nd


def purge_runtime(root: Path) -> list[str]:
    """删掉构建/自检过程在现场生成的运行期残留，返回被删的相对路径列表。

    为什么需要（2026-11-24 实测）：自检里跑了一次
    `python.exe agent/warehouse_agent.py --selftest`，它按设计往
    BUILD_DIR/debug/wh_agent.log 写了一行日志，于是 `debug/wh_agent.log`
    直接进了 zip —— 上一轮没有这条自检，所以没人发现这个路径。

    exe 启动时也会自己造 appsettings.json / logs/ / debug/ / config/instances/ /
    config/config.json / backup/ / temp/。这些都不属于发布内容。
    """
    gone: list[str] = []
    for name in ("debug", "logs", "report", "temp", "backup"):
        d = root / name
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            gone.append(name + "/")
    for rel in ("appsettings.json", "config/config.json"):
        f = root / rel
        if f.is_file():
            f.unlink()
            gone.append(rel)
    inst = root / "config" / "instances"
    if inst.is_dir():
        shutil.rmtree(inst, ignore_errors=True)
        gone.append("config/instances/")
    return gone


def verify_zip(zip_path: Path) -> tuple[bool, str]:
    """打包后再验一遍 zip 本身，返回 (是否通过, 说明)。"""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    problems: list[str] = []
    for nm in names:
        if "\\" in nm:
            problems.append(f"反斜杠分隔符: {nm}")
        if "__pycache__" in nm or nm.endswith((".pyc", ".pyo")):
            problems.append(f"pyc 泄漏: {nm}")
        parts = Path(nm).parts
        if nm.startswith("/") or ".." in parts:
            problems.append(f"非法路径: {nm}")
        head_part = parts[0]
        if head_part in ("debug", "logs", "report", "temp", "backup"):
            problems.append(f"运行期残留: {nm}")
        if nm in ("appsettings.json", "config/config.json"):
            problems.append(f"运行期残留: {nm}")
        if nm.startswith("config/instances/"):
            problems.append(f"运行期残留: {nm}")
        if len(problems) >= 5:
            break
    head = f"{len(names)} 条目, {zip_path.stat().st_size / 1024 / 1024:.1f} MB"
    if problems:
        return False, head + "；问题: " + "; ".join(problems)
    return True, head + "，无反斜杠 / 无 pyc / 无越界路径"


# ============================ 各步骤 ============================

def step_ui() -> None:
    """通用 UI 运行库：libs / runtimes / plugins + 根目录几个 dll。"""
    log("[1/10] 通用 UI 运行库")
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
    log("[2/10] 主程序与图标")
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
        # 写图标改了 exe 内容，跟源比必然不等；这条侧链靠 set_exe_icon 自己的
        # 「图标资源数」返回值兜底，不再参与逐字节对账。
        _forget(APP_EXE_DST)
    except Exception as e:  # 图标失败不该让整包作废
        log(f"  ! 写图标失败（不影响运行）：{type(e).__name__}: {e}")


def step_maa_single_source() -> None:
    """把两套 MaaFramework 合成一套：统一用 maa/bin 那版（更新的 5.16.1）。

    背景（2026-11-24 实测）：发布包里原本并存两套同名原生库——
      runtimes/win-x64/native/            19 个 dll，MaaFramework 5.14.3（界面用）
      python/Lib/site-packages/maa/bin/   21 个 dll，MaaFramework 5.16.1（agent 用）
    同名 19 个里内容完全相同只有 4 个（DirectML / dxcompiler / dxil / ViGEmClient），
    其余 15 个都是不同版本；maa/bin 还多 MaaIOSControlUnit.dll、xdb_client.dll、
    plugins/。两套各 86 MB，压缩后各约 35 MB —— 白扛一倍体积。

    做法：
      1. 用 maa/bin 的文件覆盖 runtimes/win-x64/native（覆盖后 21 个文件）；
      2. 删掉 maa/bin 里的 dll，只留 plugins/（它是随包分发的插件示例，不是原生库）；
      3. agent 端靠 agent/warehouse_agent.py 自设 MAAFW_BINARY_PATH 指向 runtimes。

    已验证（同一台机器，UI 与 agent 都指向 runtimes 这套 5.16.1）：
      * 界面日志连打 `MaaFramework 版本：v5.16.1`；
      * `[src=Worker] Agent 标识符：HpDaC0P3` / `Agent 客户端哈希：4291050`；
      * debug/wh_agent.log 打出两个 `已注册 custom action`；
      * maafw.log 有 `connected to 127.0.0.1:16384` 与 ScreencapAgent method_set。
    也就是说 MFAAvalonia 的绑定层（Maa.Framework.Binding 5.10.0）驱动 5.16.1 没问题
    —— 它按文件名找库、不校验版本号（见 libs/MFAAvalonia.Core.dll 的路径表）。
    """
    log("[5/10] MaaFramework 合并成单套")
    src = ROOT / "python" / "Lib" / "site-packages" / "maa" / "bin"
    dst = BUILD_DIR / "runtimes" / "win-x64" / "native"
    if not src.is_dir():
        log(f"  ! 缺 {src.relative_to(ROOT)}，跳过（包里会保留 runtimes 自带那套）")
        return
    if not dst.is_dir():
        log(f"  ! 缺 {dst.relative_to(BUILD_DIR)}，跳过")
        return

    dlls = [p for p in src.iterdir() if p.is_file()]
    if not dlls:
        log("  ! maa/bin 里没有 dll（已被合并过？），跳过")
        return

    n = b = 0
    newer = 0
    for p in dlls:
        t = dst / p.name
        old = t.stat().st_size if t.is_file() else -1
        if old != p.stat().st_size:
            newer += 1
        b += copy_file(p, t)
        n += 1
        _forget(t.relative_to(BUILD_DIR).as_posix())  # 目标是覆盖写入，跟「源=maa/bin」对账
    log(f"  覆盖 {n} 个 dll 进 runtimes/win-x64/native（其中 {newer} 个体积有变化）")

    # 删掉 maa/bin 里的 dll：留一份就是 86 MB，agent 已改走 runtimes。
    removed = rb = 0
    for p in dlls:
        t = BUILD_DIR / "python" / "Lib" / "site-packages" / "maa" / "bin" / p.name
        if t.is_file():
            rb += t.stat().st_size
            t.unlink()
            _forget(t.relative_to(BUILD_DIR).as_posix())
            removed += 1
    log(f"  从 maa/bin 移除 {removed} 个 dll，省 {rb / 1024 / 1024:.1f} MB")


def step_python() -> bool:
    """嵌入式 Python + site-packages。返回是否真的打进了 python/。"""
    log("[4/10] Python 运行时")
    src = ROOT / "python"
    if not src.is_dir():
        # 这里以前只打一行警告就 return，main() 照旧 return 0，会「成功」出一个
        # 没有 Python 的包 —— agent 拿它根本跑不起来，而自检那条又被跳过。
        # 改成致命错误：宁可在 CI 上大声失败，也不要发出半成品。
        fail("缺 python/ 目录（先由工作流下载嵌入式 Python 并 pip install --target）")
        log("    包里不会有 Python，agent 无法运行；已记为致命错误")
        return False
    n, b = copytree(src, BUILD_DIR / "python")
    log(f"  python/    {n:>5} 文件  {b / 1024 / 1024:>8.1f} MB")
    return True


def step_resource() -> None:
    log("[6/10] 资源包")
    n, b = copytree(ROOT / "resource", BUILD_DIR / "resource")
    log(f"  resource/  {n:>5} 文件  {b / 1024 / 1024:>8.1f} MB")


def step_code() -> None:
    log("[7/10] 代码与数据")
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


def step_assets() -> None:
    """Assets/logo.ico —— MFAAvalonia 的窗口图标与托盘图标走这里。

    MFAAvalonia README「自定义图标」：把图标放在程序根的 `Assets/logo.ico`。
    没有这个文件时它回落到内嵌的 `avaloniares://MFAAvalonia.Core/Assets/logo.ico`，
    也就是 MFAAvalonia 自己的 logo —— 只有 exe 的 PE 图标被换了，任务栏预览、
    标题栏、托盘仍是官方图标（2026-11-24 实测确认过这个回落）。
    """
    log("[3/10] Assets/logo.ico")
    icon = ROOT / ICON_SRC
    if not icon.is_file():
        log(f"  ! 缺 {ICON_SRC}，跳过（窗口与托盘会用 MFAAvalonia 内置图标）")
        return
    try:
        import io

        from PIL import Image

        sizes = [16, 24, 32, 48, 64, 96, 128, 256]
        im = Image.open(icon).convert("RGBA")
        frames = [im.resize((s, s), Image.LANCZOS) for s in sizes]
        buf = io.BytesIO()
        frames[-1].save(buf, format="ICO", sizes=[(s, s) for s in sizes])
        out = BUILD_DIR / "Assets" / "logo.ico"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(buf.getvalue())
        log(f"  Assets/logo.ico {out.stat().st_size:,} B（{len(sizes)} 个尺寸）")
    except Exception as e:
        log(f"  ! 生成 Assets/logo.ico 失败（不影响运行）：{type(e).__name__}: {e}")


def step_config() -> None:
    """默认配置：config.template.json 用干净模板写字面值，maa_option.json 直接拷源。

    **写 template 而不是 config.json**（MFAAvalonia README「MFAAvalonia 扩展字段」
    一节）：`config/config.template.json` 只在「目录里不存在其它配置 JSON（含
    config/instances 下的实例配置）」时才被转成 config.json；一旦已有用户配置就
    只读不写。反过来，如果包里直接放 config.json，程序会把它当成「用户已有的
    配置」，用户下次解压覆盖升级时，自己改过的语言/主题/布局会被静默盖掉。
    2026-11-24 实测两条日志：全新安装 → `已写入首装种子并保留背景模板：[...]`；
    已有配置 → `检测到已有用户配置，只读取持续背景默认，不重新应用首装种子。`

    内容也**不拷开发机那份** —— 里面是 UI 运行时状态（窗口位置 -32000、
    1440×781、HasCompletedFirstUseTutorial=true、布局哈希），拷进去等于把
    「这台机器已经用过」的事实发给每个用户。MFAAvalonia 自己会兜住非法的
    窗口位置（日志里有「不在有效屏幕范围内，使用默认居中位置」），但教程跳过
    和布局被钉死这两件事兜不住。
    """
    log("[8/10] 默认配置")
    cfg_dir = BUILD_DIR / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    out = cfg_dir / "config.template.json"
    out.write_text(
        json.dumps(CLEAN_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(f"  config/config.template.json（干净模板，{len(CLEAN_CONFIG)} 个键，{out.stat().st_size} B）")

    src = ROOT / "config" / "maa_option.json"
    if src.is_file():
        copy_file(src, cfg_dir / "maa_option.json")
        log("  config/maa_option.json")
    else:
        log("  ! 缺 config/maa_option.json（调试开关将用程序内置默认值）")


def step_interface(version: str) -> None:
    """拷 interface.json，改 child_exec，写 version。"""
    log("[9/10] interface.json")
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
    log("[10/10] 压缩")
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
    ap.add_argument("--keep", action="store_true",
                    help="保留旧的 install/ 内容（默认先清空）。警告：旧残留会原样进包")
    args = ap.parse_args()

    tag = args.version if args.version.startswith("v") else f"v{args.version}"

    log(f"=== 打包 {DEFAULT_PKG} {tag} {args.os}-{args.arch} ===")
    log(f"源目录: {ROOT}")

    if BUILD_DIR.exists() and not args.keep:
        shutil.rmtree(BUILD_DIR)
        log("已清空旧的 install/")
    elif BUILD_DIR.exists() and args.keep:
        # copytree 是「覆盖式」写入，不会删目标里源没有的文件。上一次构建自检生成的
        # pyc、上一次才有的模块、已经删掉的资源，都会原样留在 install/ 里并进 zip。
        # 2026-11-24 那版产物 325 个 pyc 正是这样来的。--keep 语义是「复用大件、
        # 别重拷 python/」，不是「把上次的垃圾也带上」，所以这里先清掉明显的残留。
        nf, nd = purge_pyc(BUILD_DIR)
        log(f"--keep：已清旧 install/ 里的 {nf} 个 pyc / {nd} 个空 __pycache__")
        log("--keep：注意 install/ 中源目录已不存在的旧文件不会被删除，出货前请确认")
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
    step_assets()
    if args.no_python:
        log("[4/10] Python 运行时 —— 按 --no-python 跳过（用户需自装 Python 3.13）")
        has_python = False
    else:
        has_python = step_python()
    step_maa_single_source()
    step_resource()
    step_code()
    step_config()
    step_interface(args.version)

    n, b = size_of(BUILD_DIR)
    log("")
    log(f"=== install/ 合计 {n} 文件, {b / 1024 / 1024:.1f} MB ===")

    # ---- 自检：必须在压缩之前 ----
    # 以前自检排在 step_zip 后面，自检失败时 zip 已经写盘；CI 靠退出码不上传，
    # 但本地会留下一个「自检未过」的 dist zip，而它看上去和好包没区别。
    log("\n=== 自检 ===")
    must = [
        "interface.json", "resource/pipeline", "resource/model/ocr/det.onnx",
        "agent/warehouse_agent.py", "tools/wh_scan.py", "tools/wh_counts.py",
        "data/digits/d0.png", "data/whitelist.json",
        "libs", "runtimes", APP_EXE_DST,
        "README.md", "LICENSE",
        # 单套 MaaFramework：原生库只在 runtimes 这一处，maa/bin 里应当为空
        "runtimes/win-x64/native/MaaFramework.dll",
        "config/config.template.json",
        "Assets/logo.ico",
    ]
    for m in must:
        p = BUILD_DIR / m
        if p.exists():
            log(f"  OK   {m}")
        else:
            fail(f"缺 {m}")

    # ---- 内容对账：不是「在不在」，是「对不对」 ----
    cn, ch = check_content()
    log(f"  对账 {cn} 个拷贝件，其中 {ch} 个 ≥1MB 的做了 sha256")

    if has_python:
        py = BUILD_DIR / "python" / "python.exe"
        # 自检必须模拟「最终用户」的环境。
        # 2026-11-24 两条都踩过：
        #   ① 早先这里写 MAAFW_BINARY_PATH=str(ROOT/"maafw")，而 maafw/ 既不进发布包
        #      也不进 git（.gitignore:49），CI checkout 后必定抛
        #      FileNotFoundError: `<install>/maafw` does not exist，整包判失败。
        #   ② 合并成单套之后 maa/bin 里的 dll 已被清空，不指路同样 FileNotFoundError。
        # 现在包里只有一套原生库，在 runtimes/win-x64/native，所以这里显式指它。
        env = {k: v for k, v in os.environ.items() if k != "MAAFW_BINARY_PATH"}
        env["MAAFW_BINARY_PATH"] = str(BUILD_DIR / "runtimes" / "win-x64" / "native")
        r = subprocess.run(
            [str(py), "-c", "import numpy,cv2,PIL,openpyxl,jsonschema,maa;print('ok')"],
            capture_output=True, text=True, env=env,
        )
        ok = r.returncode == 0 and "ok" in r.stdout
        if ok:
            log("  OK   python 依赖 import 自检")
        else:
            fail("python 依赖 import 自检")
            log(f"       stdout={r.stdout.strip()[:200]}")
            log(f"       stderr={r.stderr.strip()[:300]}")

        # agent 端不靠外部环境变量：warehouse_agent.py 自己把 MAAFW_BINARY_PATH
        # 指向 runtimes。这里用「清空环境变量」跑一次 --selftest，验证那条自设
        # 逻辑真的生效 —— 否则两个 Custom 动作会静默注册失败（2026-11-24 事故）。
        env2 = {k: v for k, v in os.environ.items() if k != "MAAFW_BINARY_PATH"}
        r2 = subprocess.run(
            [str(py), str(BUILD_DIR / "agent" / "warehouse_agent.py"), "--selftest"],
            capture_output=True, text=True, env=env2, cwd=str(BUILD_DIR),
        )
        blob = (r2.stdout or "") + (r2.stderr or "")
        if "ModuleNotFoundError" in blob or "does not exist" in blob:
            fail("agent 原生库自设失效（warehouse_agent.py 未能找到 MaaFramework.dll）")
            log(f"       {blob.strip()[:400]}")
        else:
            log("  OK   agent 能自己找到 runtimes 里的原生库")
    else:
        log("  --   python/ 未打包，跳过依赖自检")

    # ---- 洁净：清掉自检现场生成的 pyc ----
    # 上面那次 import 会在 install/python/Lib/site-packages 下写几百个 __pycache__，
    # 必须在压缩前删掉，否则包里就有 pyc。
    np_, nd_ = purge_pyc(BUILD_DIR)
    if np_ or nd_:
        log(f"  已清理自检产生的 {np_} 个 pyc / {nd_} 个空 __pycache__")
    else:
        log("  无 pyc 残留")

    # ---- 洁净：清掉自检现场生成的运行期文件 ----
    # agent 自检会写 debug/wh_agent.log；这条自检是 2026-11-24 才加的，
    # 不加这步的话 debug/wh_agent.log 会被打进 zip。
    gone = purge_runtime(BUILD_DIR)
    if gone:
        log(f"  已清理运行期残留: {', '.join(gone)}")
    else:
        log("  无运行期残留")

    if _bad:
        log(f"\n=== 自检未通过（{_bad} 项）—— 不产出 zip ===")
        log(f"（install/ 目录保留，便于排查：{BUILD_DIR}）")
        return 2

    # ---- 压缩 ----
    if args.no_zip:
        log(f"\n产物目录: {BUILD_DIR}")
        return 0

    out = step_zip(DEFAULT_PKG, tag, args.os, args.arch)
    ok, detail = verify_zip(out)
    if ok:
        log(f"  校验 OK：{detail}")
        log(f"\n产物: {out}")
        return 0
    log(f"  校验 FAIL：{detail}")
    log("! zip 已删除，请先修掉上面列出的问题")
    try:
        out.unlink()
    except OSError:
        pass
    return 2


if __name__ == "__main__":
    sys.exit(main())
