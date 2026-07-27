"""T-BUS：房间事件总线（发布/订阅、持久化回放、ToolReceipt、Idle/Awake）。

白板变更的唯一广播通道；按 roomId 隔离；禁止点对点私信旁路（NG-05）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from multi_agent_room.event_types import BUS_EVENT_TYPES, MERGE_PAIR, VERDICT_KINDS
from multi_agent_room.logging_setup import log_event
from multi_agent_room.paths import get_data_dir

Subscriber = Callable[["BusEvent"], None]


@dataclass
class BusEvent:
    id: str
    room_id: str
    seq: int
    ts: float
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BusEvent":
        return cls(
            id=raw["id"],
            room_id=raw["room_id"],
            seq=int(raw["seq"]),
            ts=float(raw["ts"]),
            type=raw["type"],
            payload=dict(raw.get("payload") or {}),
        )


@dataclass
class _Subscription:
    sub_id: str
    room_id: str  # "*" = 全房间（仅宿主调试；Worker 应按 room 订阅）
    types: Optional[frozenset[str]]
    callback: Subscriber


def bus_dir() -> Path:
    d = get_data_dir() / "bus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bus_log_path(room_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in room_id)
    return bus_dir() / f"{safe}.jsonl"


class EventBus:
    """房间内 pub/sub + JSONL 持久化（对接 M8 回放）。"""

    def __init__(self, *, persist: bool = True) -> None:
        self._persist = persist
        self._lock = threading.RLock()
        self._seq: dict[str, int] = {}
        self._history: dict[str, list[BusEvent]] = {}
        self._subs: dict[str, _Subscription] = {}
        self._idle: dict[str, bool] = {}  # True=Idle

    # ---- T-BUS-01 发布/订阅 ----
    def subscribe(
        self,
        room_id: str,
        callback: Subscriber,
        *,
        types: Optional[set[str] | frozenset[str] | list[str]] = None,
    ) -> str:
        """订阅某房间事件；types=None 表示该房全部类型。"""
        if room_id != "*" and not room_id:
            raise ValueError("room_id 不能为空")
        type_set = frozenset(types) if types is not None else None
        if type_set is not None:
            unknown = type_set - BUS_EVENT_TYPES
            if unknown:
                raise ValueError(f"未知订阅类型: {sorted(unknown)}")
        sub_id = f"sub-{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._subs[sub_id] = _Subscription(
                sub_id=sub_id,
                room_id=room_id,
                types=type_set,
                callback=callback,
            )
        return sub_id

    def unsubscribe(self, sub_id: str) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)

    def publish(
        self,
        room_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        ts: Optional[float] = None,
    ) -> BusEvent:
        if not room_id:
            raise ValueError("room_id 不能为空")
        if event_type not in BUS_EVENT_TYPES:
            raise ValueError(f"非法总线事件类型: {event_type}（非白板类型或未登记）")
        if event_type == "Verdict":
            kind = (payload or {}).get("verdict")
            if kind not in VERDICT_KINDS:
                raise ValueError(f"Verdict.verdict 必须为 {sorted(VERDICT_KINDS)}")

        with self._lock:
            seq = self._seq.get(room_id, 0) + 1
            self._seq[room_id] = seq
            ev = BusEvent(
                id=f"bev-{uuid.uuid4().hex[:12]}",
                room_id=room_id,
                seq=seq,
                ts=time.time() if ts is None else ts,
                type=event_type,
                payload=dict(payload or {}),
            )
            self._history.setdefault(room_id, []).append(ev)
            if self._persist:
                self._append_file(ev)
            subs = list(self._subs.values())

        self._dispatch(ev, subs)
        log_event("bus_publish", f"{event_type} seq={ev.seq}", room_id=room_id)
        return ev

    def _dispatch(self, ev: BusEvent, subs: list[_Subscription]) -> None:
        for sub in subs:
            if sub.room_id not in (ev.room_id, "*"):
                continue
            if sub.types is not None and ev.type not in sub.types:
                continue
            try:
                sub.callback(ev)
            except Exception as exc:  # noqa: BLE001 — 订阅者故障不拖垮总线
                log_event(
                    "bus_subscriber_error",
                    f"sub={sub.sub_id} err={exc}",
                    room_id=ev.room_id,
                )

    # ---- 禁止私信旁路（NG-05）----
    def send_direct(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(
            "禁止点对点私信：模型间只能经房间事件总线（NG-05）"
        )

    # ---- 合入配对（验收：DocVersion + QueueUpdated）----
    def emit_after_merge(
        self,
        room_id: str,
        *,
        doc_version: int,
        patch_id: str = "",
        queue: Optional[list[Any]] = None,
        block_ids: Optional[list[str]] = None,
    ) -> tuple[BusEvent, BusEvent]:
        """合入后必须成对发布 DocVersion 与 QueueUpdated。"""
        dv = self.publish(
            room_id,
            "DocVersion",
            {
                "version": doc_version,
                "patch_id": patch_id,
                "block_ids": list(block_ids or []),
                "reason": "merge",
            },
        )
        qu = self.publish(
            room_id,
            "QueueUpdated",
            {
                "queue": list(queue if queue is not None else []),
                "after_patch": patch_id,
                "doc_version": doc_version,
            },
        )
        assert (dv.type, qu.type) == MERGE_PAIR
        return dv, qu

    def emit_verdict(
        self, room_id: str, verdict: str, **payload: Any
    ) -> BusEvent:
        return self.publish(
            room_id, "Verdict", {"verdict": verdict, **payload}
        )

    # ---- T-BUS-04 ToolReceipt ----
    def publish_tool_receipt(
        self,
        room_id: str,
        *,
        tool_name: str,
        ok: bool,
        exit_code: Optional[int] = None,
        paths: Optional[list[str]] = None,
        diff_summary: str = "",
        mcp_id: str = "",
        **extra: Any,
    ) -> BusEvent:
        """MCP-06 预留：工具/MCP 回执进总线。"""
        return self.publish(
            room_id,
            "ToolReceipt",
            {
                "tool_name": tool_name,
                "ok": ok,
                "exit_code": exit_code,
                "paths": list(paths or []),
                "diff_summary": diff_summary,
                "mcp_id": mcp_id,
                **extra,
            },
        )

    # ---- T-BUS-05 RoomIdle / RoomAwake ----
    def signal_idle(self, room_id: str, *, reason: str = "") -> BusEvent:
        with self._lock:
            self._idle[room_id] = True
        return self.publish(
            room_id, "RoomIdle", {"reason": reason or "no_user_question"}
        )

    def signal_awake(self, room_id: str, *, reason: str = "") -> BusEvent:
        with self._lock:
            self._idle[room_id] = False
        return self.publish(
            room_id, "RoomAwake", {"reason": reason or "user_ask"}
        )

    def is_idle(self, room_id: str) -> bool:
        return bool(self._idle.get(room_id, True))

    # ---- T-BUS-03 持久化与回放 ----
    def history(self, room_id: str) -> list[BusEvent]:
        with self._lock:
            return list(self._history.get(room_id) or [])

    def replay(self, room_id: str, *, from_disk: bool = False) -> list[BusEvent]:
        """按 seq 还原事件顺序（供 M8 回放）。"""
        if from_disk:
            events = self._load_file(room_id)
        else:
            events = self.history(room_id)
        return sorted(events, key=lambda e: (e.seq, e.ts))

    def load_room_from_disk(self, room_id: str) -> list[BusEvent]:
        """启动时装入审计日志到内存。"""
        events = self._load_file(room_id)
        with self._lock:
            self._history[room_id] = list(events)
            self._seq[room_id] = max((e.seq for e in events), default=0)
        return events

    def _append_file(self, ev: BusEvent) -> None:
        path = bus_log_path(ev.room_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")

    def _load_file(self, room_id: str) -> list[BusEvent]:
        path = bus_log_path(room_id)
        if not path.exists():
            return []
        out: list[BusEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(BusEvent.from_dict(json.loads(line)))
        return out
