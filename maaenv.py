"""my-maa 启动引导：在 import maa 之前把原生库目录钉死。

必须在任何 `import maa` 之前 import 本模块。

- MAAFW_BINARY_PATH: MaaFramework 原生库目录（MaaFramework.dll / MaaToolkit.dll / ...）
- Python 绑定: 用 python/Lib/site-packages/maa（pip 装的 MaaFw），不再 vendored

原生库按顺序找，取第一个真有 MaaFramework.dll 的：
  1. 环境变量 MAAFW_BINARY_PATH（外部显式指定，最高优先级）
  2. maafw/                     —— 开发机 main.py 手工调试模式
  3. runtimes/win-x64/native    —— 发布包里那一套（与 MFAAvalonia 界面共用）
  4. 不设，交给 maa 包自己回落到 site-packages/maa/bin（它会自己报错）

2026-11-24：以前只有 maafw/ 一条路，而 maafw/ 既不进 git 也不进发布包，
于是包里 `python.exe main.py` 一启动就 FileNotFoundError: 原生库缺失。
发布包改造为只剩一套 5.16.1（放在 runtimes/）之后，这里必须认 runtimes。

2026-11-25：删掉 vendor/ 目录。它原来是 site-packages/maa 的一份**旧副本**
（17 个 .py 里 16 个 sha256 相同，仅 tasker.py 的 clear_cache() docstring 少两句）
加上一份与 site-packages/MaaAgentBinary 逐字节相同的 MaaAgentBinary（80 文件
13,139,820 B）。把 vendor 插到 sys.path[0] 的唯一效果是让 main.py 拿到旧绑定层，
而 agent 那条真实生产路径根本不 import 本模块。实测移走后 maa 自动回落
site-packages 的新版，版本号仍 v5.16.1、bundle 289 节点、截图正常。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAAFW_DIR = ROOT / "maafw"
RUNTIME_DIR = ROOT / "runtimes" / "win-x64" / "native"
BUNDLE_DIR = ROOT / "resource"
DEBUG_DIR = ROOT / "debug"
CONFIG_DIR = ROOT / "config"

_FRAMEWORK = "MaaFramework.dll" if sys.platform == "win32" else "libMaaFramework.so"


def _has_native(d: Path) -> bool:
    return (d / _FRAMEWORK).is_file()


def bootstrap() -> Path | None:
    """设置环境并返回生效的原生库目录；都找不到时返回 None（交给 maa 自己报错）。

    重复调用无副作用。
    """
    env = os.environ.get("MAAFW_BINARY_PATH")
    if env and _has_native(Path(env)):
        native: Path | None = Path(env)
    else:
        native = next((d for d in (MAAFW_DIR, RUNTIME_DIR) if _has_native(d)), None)

    if native is not None:
        os.environ["MAAFW_BINARY_PATH"] = str(native)

    for d in (DEBUG_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    return native


NATIVE_DIR = bootstrap()
# NATIVE_DIR = 实际生效的原生库目录（None = 三级回落都没命中）。
# doctor 与排障都读这个，不要各自去猜 maafw/ —— 发布包里没有 maafw/，
# 只能命中 runtimes/。以前 cmd_doctor() 硬打 maaenv.MAAFW_DIR，于是在包里
# 永远输出「缺失」，而库其实好好的（实测 v5.16.1 正常加载）。
