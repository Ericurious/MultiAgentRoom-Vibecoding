"""M2：事件时间线（T-M2-05）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoomEvent:
    ts: float
    kind: str
    summary: str
    collapsed: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


class EventTimeline:
    def __init__(self) -> None:
        self._by_room: dict[str, list[RoomEvent]] = {}

    def append(
        self,
        room_id: str,
        kind: str,
        summary: str,
        *,
        collapsed: bool = False,
        **payload: Any,
    ) -> RoomEvent:
        ev = RoomEvent(
            ts=time.time(),
            kind=kind,
            summary=summary,
            collapsed=collapsed,
            payload=dict(payload),
        )
        self._by_room.setdefault(room_id, []).append(ev)
        return ev

    def list_events(
        self, room_id: str, *, include_collapsed: bool = True
    ) -> list[RoomEvent]:
        items = list(self._by_room.get(room_id) or [])
        if include_collapsed:
            return items
        return [e for e in items if not e.collapsed]

    def clear(self, room_id: str) -> None:
        self._by_room.pop(room_id, None)
