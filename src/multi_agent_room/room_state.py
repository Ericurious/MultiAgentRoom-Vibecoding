"""T-M8b：RoomState schema 快照与恢复。"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from multi_agent_room.paths import get_data_dir


def room_state_dir(room_id: str) -> Path:
    d = get_data_dir() / "room_state" / room_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def room_state_file(room_id: str) -> Path:
    return room_state_dir(room_id) / "state.json"


def archive_root() -> Path:
    d = get_data_dir() / "archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class RoomState:
    """spec M8 最小 RoomState。"""

    room_id: str
    phase: str
    frozen: bool = False
    clarify_hold: bool = False
    doc: Optional[dict[str, Any]] = None
    queue: list[dict[str, Any]] = field(default_factory=list)
    open_rejects: list[dict[str, Any]] = field(default_factory=list)
    confirm: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, Any] = field(default_factory=dict)
    room: dict[str, Any] = field(default_factory=dict)
    write_token: Optional[dict[str, Any]] = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "roomId": self.room_id,
            "phase": self.phase,
            "frozen": self.frozen,
            "clarifyHold": self.clarify_hold,
            "doc": self.doc,
            "queue": self.queue,
            "openRejects": self.open_rejects,
            "confirm": self.confirm,
            "roles": self.roles,
            "room": self.room,
            "writeToken": self.write_token,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RoomState":
        return cls(
            room_id=str(raw.get("roomId") or raw.get("room_id") or ""),
            phase=str(raw.get("phase") or "Idle"),
            frozen=bool(raw.get("frozen", False)),
            clarify_hold=bool(raw.get("clarifyHold", raw.get("clarify_hold", False))),
            doc=raw.get("doc"),
            queue=list(raw.get("queue") or []),
            open_rejects=list(raw.get("openRejects") or raw.get("open_rejects") or []),
            confirm=dict(raw.get("confirm") or {}),
            roles=dict(raw.get("roles") or {}),
            room=dict(raw.get("room") or {}),
            write_token=raw.get("writeToken") or raw.get("write_token"),
            updated_at=float(raw.get("updatedAt") or raw.get("updated_at") or time.time()),
        )


class RoomStateStore:
    def save(self, state: RoomState) -> Path:
        state.updated_at = time.time()
        path = room_state_file(state.room_id)
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load(self, room_id: str) -> Optional[RoomState]:
        path = room_state_file(room_id)
        if not path.exists():
            return None
        return RoomState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def archive(self, room_id: str) -> Path:
        """P1a 最小归档：复制 state 到 archive/ 并登记索引。"""
        src = room_state_file(room_id)
        if not src.exists():
            raise FileNotFoundError(f"无 RoomState 可归档: {room_id}")
        dest_dir = archive_root() / room_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "state.json"
        shutil.copy2(src, dest)
        idx = archive_root() / "index.json"
        items: list[dict[str, Any]] = []
        if idx.exists():
            items = list(json.loads(idx.read_text(encoding="utf-8")).get("rooms") or [])
        items = [x for x in items if x.get("roomId") != room_id]
        items.append(
            {
                "roomId": room_id,
                "archivedAt": time.time(),
                "path": str(dest),
            }
        )
        idx.write_text(
            json.dumps({"rooms": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return dest

    def list_archived(self) -> list[dict[str, Any]]:
        idx = archive_root() / "index.json"
        if not idx.exists():
            return []
        return list(json.loads(idx.read_text(encoding="utf-8")).get("rooms") or [])
