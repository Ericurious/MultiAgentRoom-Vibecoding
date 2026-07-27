"""M3：独立 SessionHandle（T-M3-03）— 禁止共享 cache key。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SessionHandle:
    agent_id: str
    room_id: str
    session_id: str
    # 每次模型调用刷新 request_id；session_id 在本回合内稳定
    last_request_id: str = ""
    # 显式禁止跨 Agent 共享
    cache_key: Optional[str] = None  # 必须始终为 None（隔离断言）
    history: list[dict[str, Any]] = field(default_factory=list)

    def next_request(self) -> str:
        self.last_request_id = f"req-{uuid.uuid4().hex}"
        return self.last_request_id

    def append_turn(self, role: str, content: str) -> None:
        """仅写入本 Session 私有历史；不得合并他 Agent 历史。"""
        self.history.append({"role": role, "content": content, "request_id": self.last_request_id})


def create_session(agent_id: str, room_id: str) -> SessionHandle:
    return SessionHandle(
        agent_id=agent_id,
        room_id=room_id,
        session_id=f"sess-{uuid.uuid4().hex}",
        cache_key=None,
    )


class SessionRegistry:
    """房间内各 Agent 的独立会话注册表。"""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], SessionHandle] = {}

    def open(self, agent_id: str, room_id: str) -> SessionHandle:
        key = (room_id, agent_id)
        if key not in self._sessions:
            self._sessions[key] = create_session(agent_id, room_id)
        return self._sessions[key]

    def get(self, agent_id: str, room_id: str) -> Optional[SessionHandle]:
        return self._sessions.get((room_id, agent_id))

    def assert_isolated(self, room_id: str, agent_a: str, agent_b: str) -> None:
        sa = self.open(agent_a, room_id)
        sb = self.open(agent_b, room_id)
        if sa.session_id == sb.session_id:
            raise AssertionError("会话未隔离：session_id 相同")
        ra = sa.next_request()
        rb = sb.next_request()
        if ra == rb:
            raise AssertionError("请求未隔离：request_id 相同")
        if sa.cache_key is not None or sb.cache_key is not None:
            raise AssertionError("禁止共享 cache_key")
