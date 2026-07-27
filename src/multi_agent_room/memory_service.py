"""T-M9：共享记忆与私有记忆（不替代共享稿）。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.paths import get_data_dir
from multi_agent_room.sec_guard import assert_no_secret_in_text

SharedKind = Literal[
    "anchor_summary",
    "constraint",
    "final_pointer",
    "todo",
    "delivery_index",
]

BLOCKED_KEYS = frozenset(
    {"blocks", "blocks[]", "block_text", "replace", "shared_doc_body"}
)


@dataclass
class SharedMemoryItem:
    item_id: str
    room_id: str
    kind: SharedKind
    content: str
    tags: list[str] = field(default_factory=list)
    resolved: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "roomId": self.room_id,
            "kind": self.kind,
            "content": self.content,
            "tags": list(self.tags),
            "resolved": self.resolved,
            "meta": dict(self.meta),
            "createdAt": self.created_at,
        }


@dataclass
class PrivateMemoryItem:
    item_id: str
    agent_id: str
    room_id: str
    content: str
    tag: str = "draft"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "agentId": self.agent_id,
            "roomId": self.room_id,
            "content": self.content,
            "tag": self.tag,
            "createdAt": self.created_at,
        }


def _memory_dir() -> Path:
    d = get_data_dir() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


class MemoryService:
    """共享记忆 + 私有记忆；禁止经记忆旁路改共享稿 blocks。"""

    def __init__(self) -> None:
        self._shared: dict[str, list[SharedMemoryItem]] = {}
        self._private: dict[str, list[PrivateMemoryItem]] = {}  # key agent_id

    # ---- 红线 ----
    def _reject_block_bypass(self, payload: dict[str, Any]) -> None:
        keys = set(payload.keys())
        bad = keys & BLOCKED_KEYS
        if bad:
            raise PermissionError(
                f"禁止经记忆旁路改稿：不得写入 {sorted(bad)}（共享稿唯一写入口在 DocService）"
            )
        # 嵌套
        for v in payload.values():
            if isinstance(v, dict):
                nested = set(v.keys()) & BLOCKED_KEYS
                if nested:
                    raise PermissionError(
                        f"禁止经记忆旁路改稿：嵌套字段 {sorted(nested)}"
                    )
            if isinstance(v, list) and v and isinstance(v[0], dict):
                if any("block_id" in x and "text" in x for x in v if isinstance(x, dict)):
                    raise PermissionError(
                        "禁止经记忆旁路改稿：疑似 blocks[] 正文"
                    )

    # ---- 共享记忆 ----
    def write_shared(
        self,
        room_id: str,
        kind: SharedKind,
        content: str,
        *,
        tags: Optional[list[str]] = None,
        resolved: bool = False,
        meta: Optional[dict[str, Any]] = None,
        gate_passed: bool = False,
        **extra: Any,
    ) -> SharedMemoryItem:
        payload = {"kind": kind, "content": content, **(meta or {}), **extra}
        self._reject_block_bypass(payload)
        assert_no_secret_in_text(content, where="共享记忆")
        # 通过前过程稿不得标 resolved
        if resolved and not gate_passed and kind != "final_pointer":
            raise ValueError("通过前过程稿不得标 resolved（已决议）")
        if kind == "final_pointer" and not gate_passed:
            raise ValueError("终稿指针仅可在 JudgeApprove / 门禁后写入")
        item = SharedMemoryItem(
            item_id=f"sm-{uuid.uuid4().hex[:10]}",
            room_id=room_id,
            kind=kind,
            content=content.strip(),
            tags=list(tags or []),
            resolved=bool(resolved and gate_passed),
            meta=dict(meta or {}),
        )
        self._shared.setdefault(room_id, []).append(item)
        self._persist_shared(room_id)
        log_event("memory_shared_write", f"kind={kind} id={item.item_id}", room_id=room_id)
        return item

    def search_shared(
        self,
        room_id: str,
        *,
        query: str = "",
        kind: Optional[SharedKind] = None,
        viewer_agent_id: Optional[str] = None,
        allow_kinds: Optional[set[str]] = None,
    ) -> list[SharedMemoryItem]:
        """检索；allow_kinds 为空表示房间内默认可检索全部共享项。"""
        items = list(self._shared.get(room_id) or [])
        if kind:
            items = [i for i in items if i.kind == kind]
        if allow_kinds is not None:
            items = [i for i in items if i.kind in allow_kinds]
        q = (query or "").strip().lower()
        if q:
            items = [
                i
                for i in items
                if q in i.content.lower()
                or q in i.kind.lower()
                or any(q in t.lower() for t in i.tags)
            ]
        # viewer 仅用于审计日志；共享记忆对房内 Agent 默认可读（P1a）
        if viewer_agent_id:
            log_event(
                "memory_search",
                f"viewer={viewer_agent_id} n={len(items)} q={q[:40]}",
                room_id=room_id,
            )
        return items

    # ---- 私有记忆 ----
    def write_private(
        self,
        agent_id: str,
        room_id: str,
        content: str,
        *,
        tag: str = "draft",
        **extra: Any,
    ) -> PrivateMemoryItem:
        self._reject_block_bypass({"content": content, **extra})
        assert_no_secret_in_text(content, where="私有记忆")
        item = PrivateMemoryItem(
            item_id=f"pm-{uuid.uuid4().hex[:10]}",
            agent_id=agent_id,
            room_id=room_id,
            content=content,
            tag=tag,
        )
        self._private.setdefault(agent_id, []).append(item)
        self._persist_private(agent_id)
        log_event(
            "memory_private_write",
            f"agent={agent_id} id={item.item_id}",
            room_id=room_id,
        )
        return item

    def read_private(
        self, agent_id: str, *, room_id: Optional[str] = None
    ) -> list[PrivateMemoryItem]:
        items = list(self._private.get(agent_id) or [])
        if room_id:
            items = [i for i in items if i.room_id == room_id]
        return items

    def search_private(
        self,
        viewer_agent_id: str,
        *,
        room_id: str = "",
        query: str = "",
        target_agent_id: Optional[str] = None,
    ) -> list[PrivateMemoryItem]:
        """权限过滤：仅可搜自己的私有记忆；跨 Agent 恒为空。"""
        if target_agent_id and target_agent_id != viewer_agent_id:
            log_event(
                "memory_private_deny",
                f"viewer={viewer_agent_id} target={target_agent_id}",
                room_id=room_id or None,
            )
            return []
        items = self.read_private(viewer_agent_id, room_id=room_id or None)
        q = (query or "").strip().lower()
        if q:
            items = [i for i in items if q in i.content.lower() or q in i.tag.lower()]
        return items

    def assemble_private_context(
        self, viewer_agent_id: str, peer_ids: list[str], *, room_id: str = ""
    ) -> dict[str, Any]:
        """组装上下文：own 可见；peer 私有记忆恒为空列表。"""
        peers: dict[str, list[dict[str, Any]]] = {}
        for peer in peer_ids:
            if peer == viewer_agent_id:
                continue
            peers[peer] = []
        own = [
            i.to_dict()
            for i in self.read_private(viewer_agent_id, room_id=room_id or None)
        ]
        return {
            "viewer": viewer_agent_id,
            "own_private_memory": own,
            "peer_private_memory": peers,
        }

    def assert_private_no_leak(
        self, viewer_agent_id: str, peer_ids: list[str], *, room_id: str = ""
    ) -> None:
        ctx = self.assemble_private_context(
            viewer_agent_id, peer_ids, room_id=room_id
        )
        blob = json.dumps(ctx, ensure_ascii=False)
        for peer in peer_ids:
            if peer == viewer_agent_id:
                continue
            for item in self.read_private(peer, room_id=room_id or None):
                if item.content and item.content in blob:
                    raise AssertionError(
                        f"私有记忆泄露: {peer} -> {viewer_agent_id}"
                    )

    # ---- 落盘 ----
    def _persist_shared(self, room_id: str) -> None:
        path = _memory_dir() / f"shared-{room_id}.json"
        path.write_text(
            json.dumps(
                [i.to_dict() for i in self._shared.get(room_id, [])],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _persist_private(self, agent_id: str) -> None:
        path = _memory_dir() / f"private-{agent_id}.json"
        path.write_text(
            json.dumps(
                [i.to_dict() for i in self._private.get(agent_id, [])],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
