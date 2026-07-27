"""M3：Agent 持久身份（T-M3-01/02）。"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from multi_agent_room.paths import get_data_dir


@dataclass
class AgentProfile:
    agent_id: str
    display_name: str
    model_config_id: str
    persona: str = ""
    backup_config_ids: list[str] = field(default_factory=list)  # P0 仅存储
    capability_tags: list[str] = field(default_factory=list)  # P1a
    tool_allowlist: list[str] = field(default_factory=list)
    path_allowlist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AgentProfile":
        return cls(
            agent_id=raw["agent_id"],
            display_name=raw.get("display_name", ""),
            model_config_id=raw["model_config_id"],
            persona=raw.get("persona", ""),
            backup_config_ids=list(raw.get("backup_config_ids") or []),
            capability_tags=list(raw.get("capability_tags") or []),
            tool_allowlist=list(raw.get("tool_allowlist") or []),
            path_allowlist=list(raw.get("path_allowlist") or []),
        )


def agents_file() -> Path:
    return get_data_dir() / "agents.json"


def new_agent_id() -> str:
    return f"agent-{uuid.uuid4().hex[:10]}"


class AgentProfileStore:
    def list_all(self) -> list[AgentProfile]:
        return [AgentProfile.from_dict(x) for x in self._load()]

    def get(self, agent_id: str) -> Optional[AgentProfile]:
        for a in self.list_all():
            if a.agent_id == agent_id:
                return a
        return None

    def upsert(self, profile: AgentProfile) -> AgentProfile:
        items = self._load()
        for i, raw in enumerate(items):
            if raw.get("agent_id") == profile.agent_id:
                items[i] = profile.to_dict()
                self._save(items)
                return profile
        items.append(profile.to_dict())
        self._save(items)
        return profile

    def delete(self, agent_id: str) -> None:
        self._save([x for x in self._load() if x.get("agent_id") != agent_id])

    def _load(self) -> list[dict[str, Any]]:
        path = agents_file()
        if not path.exists():
            return []
        return list(json.loads(path.read_text(encoding="utf-8")).get("agents") or [])

    def _save(self, items: list[dict[str, Any]]) -> None:
        path = agents_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"agents": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
