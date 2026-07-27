"""T-PROTO：输出协议归一化（§5.8 / B-08 / DIS-11）。

自然语言草稿 ≠ 白板消息；本模块是唯一上白板/待合入队列闸门。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.private_thought import PrivateThoughtStore

Route = Literal["ready", "private", "retry", "reject"]

WORKER_KINDS = frozenset(
    {"Read", "Abstain", "SilentCheckPass", "Patch", "Clarify"}
)
JUDGE_KINDS = frozenset(
    {"Accept", "Merge", "R1", "R2", "R3", "JudgeApprove"}
)

# B-08 / DIS-11：必备字段
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "Read": ("agent_id", "version"),
    "Abstain": ("agent_id", "version"),
    "SilentCheckPass": ("agent_id", "version", "doc_id"),
    "Patch": ("target", "category", "claim", "replace"),
    "Clarify": ("question",),
    "Accept": ("agent_id",),
    "Merge": ("agent_id",),
    "R1": ("agent_id", "target"),
    "R2": ("agent_id",),
    "R3": ("agent_id",),
    "JudgeApprove": ("agent_id",),
}

# 实质类别（交 M5 前至少要有声明；琐碎过滤在 M5）
SUBSTANTIVE_CATEGORIES = frozenset(
    {
        "fact",
        "logic",
        "security",
        "api",
        "scheme",
        "fatal",
        "missing",
        "other",
    }
)

_PATCH_LIKE = re.compile(
    r"(?i)(target\s*[=:：]|replace\s*[=:：]|claim\s*[=:：]|"
    r"补丁|PATCH\b|blockId|B\d{2,}|\breplace\b)",
)
_CHAT_HINT = re.compile(
    r"(你好|哈哈|怎么样|闲聊|谢谢|ok啦|随便聊聊)",
)


@dataclass
class ProtoMessage:
    kind: str
    fields: dict[str, Any]
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.fields}


@dataclass
class NormalizeResult:
    ok: bool
    route: Route
    message: Optional[ProtoMessage] = None
    error: str = ""
    retry_hint: str = ""
    retries_left: int = 0
    entered_queue: bool = False  # 是否进入待合入公共队列


@dataclass
class ProtocolNormalizer:
    """工人/评议输出 → 结构消息；闲聊进私有思考。"""

    thoughts: PrivateThoughtStore = field(default_factory=PrivateThoughtStore)
    max_structure_retries: int = 2
    # room_id -> agent_id -> fail count
    _fail_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    # 已通过字段校验、待交 M5 的 Patch（公共待合入队列闸门后）
    _patch_queue: dict[str, list[ProtoMessage]] = field(default_factory=dict)

    # ---- 对外 API ----
    def normalize_worker(
        self,
        *,
        room_id: str,
        agent_id: str,
        text: str,
        doc_version: int = 0,
        doc_id: str = "",
    ) -> NormalizeResult:
        return self._normalize(
            room_id=room_id,
            agent_id=agent_id,
            text=text,
            allowed=WORKER_KINDS,
            role="worker",
            defaults={"agent_id": agent_id, "version": doc_version, "doc_id": doc_id},
        )

    def normalize_judge(
        self,
        *,
        room_id: str,
        agent_id: str,
        text: str,
    ) -> NormalizeResult:
        return self._normalize(
            room_id=room_id,
            agent_id=agent_id,
            text=text,
            allowed=JUDGE_KINDS,
            role="judge",
            defaults={"agent_id": agent_id},
        )

    def patch_queue(self, room_id: str) -> list[ProtoMessage]:
        return list(self._patch_queue.get(room_id) or [])

    def clear_patch_queue(self, room_id: str) -> None:
        self._patch_queue.pop(room_id, None)

    # ---- 核心 ----
    def _normalize(
        self,
        *,
        room_id: str,
        agent_id: str,
        text: str,
        allowed: frozenset[str],
        role: str,
        defaults: dict[str, Any],
    ) -> NormalizeResult:
        raw = (text or "").strip()
        if not raw:
            return self._fail(
                room_id,
                agent_id,
                raw,
                "空输出",
                private=True,
            )

        parsed = self._parse(raw)
        if parsed is None:
            # 闲聊或无法解析
            if self._looks_patch_like(raw):
                # 像补丁但无结构 → 不进公共流
                return self._fail(
                    room_id,
                    agent_id,
                    raw,
                    "像补丁但缺结构/claim：不进公共流",
                    private=True,
                    retry=True,
                )
            return self._to_private(
                room_id, agent_id, raw, reason="闲聊/非结构输出"
            )

        kind, fields = parsed
        if kind not in allowed:
            return self._fail(
                room_id,
                agent_id,
                raw,
                f"{role} 不得发出 {kind}",
                private=True,
                retry=True,
            )

        # 填默认
        for k, v in defaults.items():
            fields.setdefault(k, v)
        fields.setdefault("agent_id", agent_id)

        missing = self._missing_fields(kind, fields)
        if missing:
            # 缺 claim 的像补丁：明确不进队列
            return self._fail(
                room_id,
                agent_id,
                raw,
                f"缺必备字段: {', '.join(missing)}（DIS-11/B-08）",
                private=True,
                retry=True,
            )

        if kind == "Patch":
            cat = str(fields.get("category") or "").strip().lower()
            if cat and cat not in SUBSTANTIVE_CATEGORIES:
                # 未知类别仍可交 M5，但打日志；空类别已在 missing 拦
                pass
            claim = str(fields.get("claim") or "").strip()
            if not claim:
                return self._fail(
                    room_id,
                    agent_id,
                    raw,
                    "Patch 缺 claim：不进公共流",
                    private=True,
                    retry=True,
                )

        msg = ProtoMessage(kind=kind, fields=fields, raw=raw)
        # 成功：清零重试计数
        self._fail_counts.setdefault(room_id, {}).pop(agent_id, None)

        entered = False
        if kind == "Patch":
            self._patch_queue.setdefault(room_id, []).append(msg)
            entered = True
            log_event(
                "proto_patch_queued",
                f"target={fields.get('target')} claim={str(fields.get('claim'))[:40]}",
                room_id=room_id,
            )
        else:
            log_event("proto_ok", f"kind={kind}", room_id=room_id)

        return NormalizeResult(
            ok=True,
            route="ready",
            message=msg,
            entered_queue=entered,
        )

    def _parse(self, raw: str) -> Optional[tuple[str, dict[str, Any]]]:
        # 1) JSON
        if raw.startswith("{") or raw.startswith("["):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict):
                kind = obj.get("type") or obj.get("kind") or obj.get("msg_type")
                if isinstance(kind, str) and kind.strip():
                    fields = {k: v for k, v in obj.items() if k not in ("type", "kind", "msg_type")}
                    # 兼容 camelCase
                    fields = self._normalize_keys(fields)
                    return kind.strip(), fields

        # 2) 标签块：KIND\nkey: value
        m = re.match(
            r"^\s*(Read|Abstain|SilentCheckPass|Patch|Clarify|Accept|Merge|R1|R2|R3|JudgeApprove)\b",
            raw,
            re.I,
        )
        if m:
            kind = m.group(1)
            # 规范化大小写
            for k in list(WORKER_KINDS | JUDGE_KINDS):
                if k.lower() == kind.lower():
                    kind = k
                    break
            fields: dict[str, Any] = {}
            for line in raw.splitlines()[1:]:
                if ":" in line or "：" in line:
                    sep = ":" if ":" in line else "："
                    key, _, val = line.partition(sep)
                    key = key.strip()
                    val = val.strip()
                    if key:
                        fields[self._key_map(key)] = val
            return kind, fields

        return None

    def _normalize_keys(self, fields: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in fields.items():
            out[self._key_map(str(k))] = v
        return out

    def _key_map(self, key: str) -> str:
        aliases = {
            "agentId": "agent_id",
            "agentid": "agent_id",
            "docId": "doc_id",
            "docid": "doc_id",
            "patchId": "patch_id",
            "blockId": "target",
            "targetBlock": "target",
        }
        k = key.strip()
        if k in aliases:
            return aliases[k]
        # camelCase → snake
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", k).lower()
        return aliases.get(s, s)

    def _missing_fields(self, kind: str, fields: dict[str, Any]) -> list[str]:
        need = REQUIRED_FIELDS.get(kind, ())
        missing = []
        for f in need:
            val = fields.get(f)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(f)
        return missing

    def _looks_patch_like(self, raw: str) -> bool:
        return bool(_PATCH_LIKE.search(raw))

    def _to_private(
        self, room_id: str, agent_id: str, raw: str, *, reason: str
    ) -> NormalizeResult:
        self.thoughts.write(agent_id, raw, tag="chatter")
        log_event("proto_private", reason, room_id=room_id)
        return NormalizeResult(
            ok=False,
            route="private",
            error=reason,
            retry_hint="",
            entered_queue=False,
        )

    def _fail(
        self,
        room_id: str,
        agent_id: str,
        raw: str,
        error: str,
        *,
        private: bool,
        retry: bool = False,
    ) -> NormalizeResult:
        if private and raw:
            self.thoughts.write(agent_id, raw, tag="proto_reject")
        counts = self._fail_counts.setdefault(room_id, {})
        n = counts.get(agent_id, 0) + 1
        counts[agent_id] = n
        left = max(0, self.max_structure_retries - n)
        hint = ""
        route: Route = "reject"
        # 默认 2 次：第 1、2 次失败给 retry 提示；第 3 次起不再提示
        if retry and n <= self.max_structure_retries:
            route = "retry"
            hint = (
                f"请用结构重发（剩余 {left} 次）。"
                "工人示例: {\"type\":\"Patch\",\"target\":\"B01\",\"category\":\"fact\","
                "\"claim\":\"…\",\"replace\":\"…\"}；"
                "或 Read / SilentCheckPass / Abstain。"
            )
            log_event("proto_retry", f"{error} left={left}", room_id=room_id)
        else:
            if retry:
                hint = "结构重试次数已用尽，本轮不再提示；输出仅留私有思考区。"
            log_event("protocol_reject", error, room_id=room_id)
            route = "private" if private else "reject"
        return NormalizeResult(
            ok=False,
            route=route,
            error=error,
            retry_hint=hint,
            retries_left=left,
            entered_queue=False,
        )
