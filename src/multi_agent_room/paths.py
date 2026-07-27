"""本机路径约定（T-ENV-02 / T-ENV-03）。

%AppData%/MultiAgentRoom/
  config/   模型配置密文、房间策略、app.json
  data/     房间状态、审计、记忆库
  logs/     运行日志（ENV-07a）

用户工作区：用户自选盘符路径，写入 config/app.json，不强制落在 AppData 下。
"""

from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = "MultiAgentRoom"


def get_appdata_root() -> Path:
    """返回应用数据根目录，不存在则创建。"""
    base = os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Roaming")
    root = Path(base) / APP_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_config_dir() -> Path:
    d = get_appdata_root() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_data_dir() -> Path:
    d = get_appdata_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_logs_dir() -> Path:
    d = get_appdata_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def normalize_workspace_path(path: str | Path) -> Path:
    """规范化工作区路径；支持盘符路径；解析为绝对路径。"""
    p = Path(path).expanduser()
    # Windows：保留盘符；resolve 处理 .. 与相对段
    try:
        p = p.resolve(strict=False)
    except OSError:
        p = p.absolute()
    return p


def ensure_workspace_dir(path: str | Path) -> Path:
    """确保工作区目录存在并返回规范化路径。"""
    p = normalize_workspace_path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
