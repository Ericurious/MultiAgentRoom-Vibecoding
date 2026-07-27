"""运行时环境探测（源码 / 冻结 exe）。"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """PyInstaller / cx_Freeze 等打包后为 True。"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """可执行文件所在目录（冻结）或项目根（源码开发时指向含 src 的上一级由调用方决定）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def meipass_dir() -> Path | None:
    """PyInstaller 解压临时目录（onefile）；onedir 时通常不需要。"""
    mp = getattr(sys, "_MEIPASS", None)
    return Path(mp) if mp else None
