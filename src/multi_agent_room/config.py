"""应用配置持久化（T-ENV-02 / T-ENV-03）。重启后可加载。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from multi_agent_room.paths import get_config_dir, normalize_workspace_path


@dataclass
class AppConfig:
    """本机应用级配置（非模型 Key；Key 属 SEC/M1）。"""

    workspace_path: str | None = None
    window_width: int = 960
    window_height: int = 640
    # 房间元数据占位：后续 M2 扩展；此处仅证明可绑定工作区
    rooms: list[dict[str, Any]] = field(default_factory=list)

    def set_workspace(self, path: str | Path) -> Path:
        normalized = normalize_workspace_path(path)
        self.workspace_path = str(normalized)
        return normalized

    def bind_room_workspace(self, room_id: str, path: str | Path) -> None:
        """房间绑定工作区路径（ENV 验收：选择盘符路径后被房间记住）。"""
        normalized = str(normalize_workspace_path(path))
        self.workspace_path = normalized
        for room in self.rooms:
            if room.get("room_id") == room_id:
                room["workspace_path"] = normalized
                return
        self.rooms.append({"room_id": room_id, "workspace_path": normalized})


def config_file_path() -> Path:
    return get_config_dir() / "app.json"


def load_config() -> AppConfig:
    path = config_file_path()
    if not path.exists():
        return AppConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AppConfig(
        workspace_path=raw.get("workspace_path"),
        window_width=int(raw.get("window_width", 960)),
        window_height=int(raw.get("window_height", 640)),
        rooms=list(raw.get("rooms") or []),
    )


def save_config(config: AppConfig) -> Path:
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
