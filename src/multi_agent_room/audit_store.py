"""T-M8b：AuditEvent 持久化与回放。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from multi_agent_room.paths import get_data_dir


@dataclass
class AuditEvent:
    id: str
    room_id: str
    ts: float
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "roomId": self.room_id,
            "ts": self.ts,
            "type": self.type,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AuditEvent":
        return cls(
            id=str(raw.get("id") or ""),
            room_id=str(raw.get("roomId") or raw.get("room_id") or ""),
            ts=float(raw.get("ts") or 0),
            type=str(raw.get("type") or ""),
            payload=dict(raw.get("payload") or {}),
        )


def audit_dir(room_id: Optional[str] = None) -> Path:
    base = get_data_dir() / "audit"
    base.mkdir(parents=True, exist_ok=True)
    if room_id:
        d = base / room_id
        d.mkdir(parents=True, exist_ok=True)
        return d
    return base


def audit_file(room_id: str) -> Path:
    return audit_dir(room_id) / "events.jsonl"


class AuditStore:
    """按房间 JSONL 追加；支持按类型过滤回放。"""

    def __init__(self) -> None:
        self._mem: dict[str, list[AuditEvent]] = {}

    def append(
        self,
        room_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        ts: Optional[float] = None,
        event_id: Optional[str] = None,
    ) -> AuditEvent:
        ev = AuditEvent(
            id=event_id or f"aud-{uuid.uuid4().hex[:12]}",
            room_id=room_id,
            ts=time.time() if ts is None else ts,
            type=event_type,
            payload=dict(payload or {}),
        )
        self._mem.setdefault(room_id, []).append(ev)
        path = audit_file(room_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        return ev

    def load(self, room_id: str) -> list[AuditEvent]:
        path = audit_file(room_id)
        items: list[AuditEvent] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                items.append(AuditEvent.from_dict(json.loads(line)))
        self._mem[room_id] = items
        return list(items)

    def replay(
        self,
        room_id: str,
        *,
        types: Optional[Iterable[str]] = None,
        reload: bool = True,
    ) -> list[AuditEvent]:
        """回放：按时间序返回事件；可按 type 过滤（如 Accept / R1）。"""
        items = self.load(room_id) if reload else list(self._mem.get(room_id) or [])
        if types is not None:
            allow = set(types)
            items = [e for e in items if e.type in allow]
        items.sort(key=lambda e: (e.ts, e.id))
        return items

    def summarize(self, room_id: str) -> list[str]:
        """UI/测试用短摘要行。"""
        lines = []
        for e in self.replay(room_id):
            hint = ""
            if e.type in ("Accept", "PatchAccepted"):
                hint = f" target={e.payload.get('target')} patch={e.payload.get('patch_id')}"
            elif e.type in ("R1", "OpenReject"):
                hint = f" target={e.payload.get('target')} reason={str(e.payload.get('reason') or '')[:40]}"
            elif e.type == "Verdict":
                hint = f" verdict={e.payload.get('verdict')}"
            lines.append(f"{e.type}@{e.ts:.0f}{hint}")
        return lines
