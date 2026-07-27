"""M3：私有思考区（他模不可见）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PrivateThoughtStore:
    _by_agent: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def write(self, agent_id: str, text: str, *, tag: str = "draft") -> None:
        self._by_agent.setdefault(agent_id, []).append({"tag": tag, "text": text})

    def read(self, agent_id: str) -> list[dict[str, Any]]:
        return list(self._by_agent.get(agent_id) or [])

    def assemble_context_for(self, viewer_agent_id: str, peer_ids: list[str]) -> dict[str, Any]:
        """组装给 viewer 的上下文：不得包含他模私有思考。"""
        peers_leak = {}
        for peer in peer_ids:
            if peer == viewer_agent_id:
                continue
            # 故意不注入 peer 私有思考
            peers_leak[peer] = []
        return {
            "viewer": viewer_agent_id,
            "own_thoughts": self.read(viewer_agent_id),
            "peer_thoughts": peers_leak,
        }

    def assert_no_leak(self, viewer_agent_id: str, peer_ids: list[str]) -> None:
        ctx = self.assemble_context_for(viewer_agent_id, peer_ids)
        for peer, thoughts in ctx["peer_thoughts"].items():
            if thoughts:
                raise AssertionError(f"私有思考泄露: {peer} -> {viewer_agent_id}")
            # 双检：peer 确有内容但未出现在上下文
            if self.read(peer):
                blob = json_dumps_safe(ctx)
                for item in self.read(peer):
                    if item["text"] and item["text"] in blob:
                        raise AssertionError("私有思考文本出现在他模上下文")


def json_dumps_safe(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
