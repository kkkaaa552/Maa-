"""my-maa 启动引导：在 import maa 之前把原生库目录与 Python 绑定钉死。

必须在任何 `import maa` 之前 import 本模块。

- MAAFW_BINARY_PATH: MaaFramework 原生库目录（MaaFramework.dll / MaaToolkit.dll / ...）
- sys.path: vendored 的 maa 绑定与 MaaAgentBinary（minitouch/maatouch/minicap）

原生库按顺序找，取第一个真有 MaaFramework.dll 的：
  1. 环境变量 MAAFW_BINARY_PATH（外部显式指定，最高优先级）
  2. maafw/                     —— 开发机 main.py 手工调试模式
  3. runtimes/win-x64/native    —— 发布包里那一套（与 MFAAvalonia 界面共用）
  4. 不设，交给 maa 包自己回落到 site-packages/maa/bin（它会自己报错）

2026-11-24：以前只有 maafw/ 一条路，而 maafw/ 既不进 git 也不进发布包，
于是包里 `python.exe main.py` 一启动就 FileNotFoundError: 原生库缺失。
发布包改造为只剩一套 5.16.1（放在 runtimes/）之后，这里必须认 runtimes。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAAFW_DIR = ROOT / "maafw"
RUNTIME_DIR = ROOT / "runtimes" / "win-x64" / "native"
VENDOR_DIR = ROOT / "vendor"
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

    if str(VENDOR_DIR) not in sys.path and VENDOR_DIR.is_dir():
        sys.path.insert(0, str(VENDOR_DIR))

    for d in (DEBUG_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    return native


bootstrap()
