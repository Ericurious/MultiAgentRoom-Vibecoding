"""M1：ModelConfig 实体与持久化（不含 Key 明文）。"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from multi_agent_room.paths import get_config_dir

ModelStatus = Literal["unknown", "probing", "ready", "failed"]
ProbeErrorCode = Literal["timeout", "auth", "network", "adapter", ""]


@dataclass
class ModelConfig:
    config_id: str
    provider_id: str = "openai_compat"
    display_name: str = ""
    api_key_ref: str = ""
    base_url: str = ""
    model_id: str = ""
    timeout_ms: int = 15000
    enabled: bool = True
    status: ModelStatus = "unknown"
    last_probe_at: Optional[str] = None
    last_error: str = ""
    last_error_code: ProbeErrorCode = ""

    def to_public_dict(self) -> dict[str, Any]:
        """可序列化到磁盘的字段（无 Key 明文）。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModelConfig":
        return cls(
            config_id=raw["config_id"],
            provider_id=raw.get("provider_id", "openai_compat"),
            display_name=raw.get("display_name", ""),
            api_key_ref=raw.get("api_key_ref", ""),
            base_url=raw.get("base_url", ""),
            model_id=raw.get("model_id", ""),
            timeout_ms=int(raw.get("timeout_ms", 15000)),
            enabled=bool(raw.get("enabled", True)),
            status=raw.get("status", "unknown"),  # type: ignore[arg-type]
            last_probe_at=raw.get("last_probe_at"),
            last_error=raw.get("last_error", ""),
            last_error_code=raw.get("last_error_code", "") or "",  # type: ignore[arg-type]
        )


def models_file() -> Path:
    return get_config_dir() / "models.json"


def new_config_id() -> str:
    return f"cfg-{uuid.uuid4().hex[:10]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ModelConfigStore:
    def list_all(self) -> list[ModelConfig]:
        return [ModelConfig.from_dict(x) for x in self._load()]

    def get(self, config_id: str) -> Optional[ModelConfig]:
        for m in self.list_all():
            if m.config_id == config_id:
                return m
        return None

    def upsert(self, cfg: ModelConfig) -> ModelConfig:
        items = self._load()
        found = False
        for i, raw in enumerate(items):
            if raw.get("config_id") == cfg.config_id:
                items[i] = cfg.to_public_dict()
                found = True
                break
        if not found:
            items.append(cfg.to_public_dict())
        self._save(items)
        return cfg

    def delete(self, config_id: str) -> None:
        items = [x for x in self._load() if x.get("config_id") != config_id]
        self._save(items)

    def _load(self) -> list[dict[str, Any]]:
        path = models_file()
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return list(raw.get("models") or [])

    def _save(self, items: list[dict[str, Any]]) -> None:
        path = models_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"models": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
