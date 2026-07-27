"""M2：房间实体与持久化（T-M2-01）。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from multi_agent_room.paths import get_data_dir

RoomPhase = Literal[
    "Idle",
    "Campaign",
    "AwaitingFirstAnswer",
    "ReviewOpen",
    "AwaitingJudge",
    "ConfirmOpen",
    "AwaitingUserEscalation",
    "Final",
    "Frozen",
    "AwaitingUserClarify",
]

PHASE_HINTS: dict[str, str] = {
    "Idle": "等待用户提问",
    "Campaign": "竞选职责中",
    "AwaitingFirstAnswer": "等待首答",
    "ReviewOpen": "审阅窗已开；下一步待合入/评判",
    "AwaitingJudge": "等待评判裁定",
    "ConfirmOpen": "确认轮进行中",
    "AwaitingUserEscalation": "预算/封顶升用户；待用户选择",
    "Final": "已通过；最终回复可写入",
    "Frozen": "用户打断：计时暂停，审阅窗不关闭",
    "AwaitingUserClarify": "待用户澄清；不计超时沉默通过",
}


@dataclass
class Room:
    room_id: str
    title: str
    workspace_path: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    phase: RoomPhase = "Idle"
    # M8a 将接管写入；M2 壳层通过 Interrupt/Resume 维护同一字段
    frozen: bool = False
    clarify_hold: bool = False
    phase_before_hold: Optional[str] = None
    invited_agent_ids: list[str] = field(default_factory=list)
    # 原话钉选
    pinned_question: Optional[str] = None
    # 资格锁展示
    first_answerer_agent_id: Optional[str] = None
    first_answerer_config_id: Optional[str] = None
    judge_agent_id: Optional[str] = None
    judge_is_user: bool = False
    # 审阅窗：Frozen/Clarify 时保持开启
    review_window_open: bool = False
    review_deadline_ts: float = 0.0
    review_seconds: int = 120
    # 澄清
    pending_clarify: Optional[str] = None
    # 最终回复槽
    final_reply: Optional[str] = None
    gate_passed: bool = False
    # M10 / M12：授权落盘令牌 {exp, scope, granted_at}
    write_token: Optional[dict[str, Any]] = None
    # M8a：默认议程（创建时由编排器写入）
    agenda: list[str] = field(default_factory=list)
    escalation_hint: str = ""
    # Web 多轮聊天记录（与共享稿审阅并行；持久化）
    chat_turns: list[dict[str, Any]] = field(default_factory=list)

    def agenda_text(self) -> str:
        hint = PHASE_HINTS.get(self.phase, self.phase)
        flags = []
        if self.frozen:
            flags.append("frozen=1")
        if self.clarify_hold:
            flags.append("clarifyHold=1")
        if self.review_window_open:
            flags.append("reviewOpen=1")
        if self.escalation_hint:
            flags.append("escalation")
        extra = (" · " + ", ".join(flags)) if flags else ""
        pipeline = ""
        if self.agenda:
            pipeline = " | 议程: " + "→".join(self.agenda)
        return f"phase={self.phase} — {hint}{extra}{pipeline}"

    def qualification_lock_text(self) -> str:
        if not self.first_answerer_config_id:
            return "资格锁：尚未首答"
        judge = "用户" if self.judge_is_user else (self.judge_agent_id or "未定")
        return (
            f"首答模型={self.first_answerer_config_id}（不可评判）"
            f" · 首答Agent={self.first_answerer_agent_id}"
            f" · 评判={judge}"
        )

    def final_slot_text(self) -> str:
        if self.phase == "Final" and self.gate_passed and self.final_reply:
            return self.final_reply
        if self.phase == "Final" and self.gate_passed:
            return ""
        return "未通过"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Room":
        return cls(
            room_id=raw["room_id"],
            title=raw.get("title", ""),
            workspace_path=raw.get("workspace_path"),
            created_at=float(raw.get("created_at") or time.time()),
            phase=raw.get("phase") or "Idle",  # type: ignore[arg-type]
            frozen=bool(raw.get("frozen", False)),
            clarify_hold=bool(raw.get("clarify_hold", False)),
            phase_before_hold=raw.get("phase_before_hold"),
            invited_agent_ids=list(raw.get("invited_agent_ids") or []),
            pinned_question=raw.get("pinned_question"),
            first_answerer_agent_id=raw.get("first_answerer_agent_id"),
            first_answerer_config_id=raw.get("first_answerer_config_id"),
            judge_agent_id=raw.get("judge_agent_id"),
            judge_is_user=bool(raw.get("judge_is_user", False)),
            review_window_open=bool(raw.get("review_window_open", False)),
            review_deadline_ts=float(raw.get("review_deadline_ts") or 0),
            review_seconds=int(raw.get("review_seconds") or 120),
            pending_clarify=raw.get("pending_clarify"),
            final_reply=raw.get("final_reply"),
            gate_passed=bool(raw.get("gate_passed", False)),
            write_token=raw.get("write_token") or raw.get("writeToken"),
            agenda=list(raw.get("agenda") or []),
            escalation_hint=raw.get("escalation_hint") or "",
            chat_turns=list(raw.get("chat_turns") or []),
        )


def new_room_id() -> str:
    return f"room-{uuid.uuid4().hex[:10]}"


def rooms_file() -> Path:
    return get_data_dir() / "rooms.json"


class RoomStore:
    """内存缓存同一 Room 实例，保证编排器与 M2 共享 frozen/phase 字段。"""

    def __init__(self) -> None:
        self._cache: dict[str, Room] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        for raw in self._load_file():
            room = Room.from_dict(raw)
            self._cache[room.room_id] = room
        self._loaded = True

    def list_all(self) -> list[Room]:
        self._ensure_loaded()
        return list(self._cache.values())

    def get(self, room_id: str) -> Optional[Room]:
        self._ensure_loaded()
        return self._cache.get(room_id)

    def upsert(self, room: Room) -> Room:
        self._ensure_loaded()
        self._cache[room.room_id] = room
        self._save_file([r.to_dict() for r in self._cache.values()])
        return room

    def _load_file(self) -> list[dict[str, Any]]:
        path = rooms_file()
        if not path.exists():
            return []
        return list(json.loads(path.read_text(encoding="utf-8")).get("rooms") or [])

    def _save_file(self, items: list[dict[str, Any]]) -> None:
        path = rooms_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"rooms": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
