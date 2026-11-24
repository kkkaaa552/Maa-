"""my-maa 启动引导：在 import maa 之前把原生库目录与 Python 绑定钉死。

必须在任何 `import maa` 之前 import 本模块。

- MAAFW_BINARY_PATH: MaaFramework 原生库目录（MaaFramework.dll / MaaToolkit.dll / ...）
- sys.path: vendored 的 maa 绑定与 MaaAgentBinary（minitouch/maatouch/minicap）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAAFW_DIR = ROOT / "maafw"
VENDOR_DIR = ROOT / "vendor"
BUNDLE_DIR = ROOT / "resource"
DEBUG_DIR = ROOT / "debug"
CONFIG_DIR = ROOT / "config"

_FRAMEWORK = "MaaFramework.dll" if sys.platform == "win32" else "libMaaFramework.so"


def bootstrap() -> Path:
    """设置环境并返回生效的原生库目录。重复调用无副作用。"""
    if not (MAAFW_DIR / _FRAMEWORK).is_file():
        raise FileNotFoundError(f"原生库缺失: {MAAFW_DIR / _FRAMEWORK}，请先运行 tools/fetch_runtime.py")

    os.environ.setdefault("MAAFW_BINARY_PATH", str(MAAFW_DIR))
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))

    for d in (DEBUG_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    return Path(os.environ["MAAFW_BINARY_PATH"])


bootstrap()
