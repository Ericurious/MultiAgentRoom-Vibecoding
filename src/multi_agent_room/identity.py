"""M3：消息身份签名（T-M3-08）— 防伪造他 Agent 消息。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SignedMessage:
    agent_id: str
    room_id: str
    msg_type: str  # Read | Patch | JudgeApprove | ...
    payload: dict[str, Any]
    ts: float
    nonce: str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "room_id": self.room_id,
            "msg_type": self.msg_type,
            "payload": self.payload,
            "ts": self.ts,
            "nonce": self.nonce,
            "signature": self.signature,
        }


class IdentityVault:
    """每 Agent 持有独立签名密钥；仅本进程内存 + 可选落盘哈希校验。"""

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def ensure_key(self, agent_id: str) -> bytes:
        if agent_id not in self._keys:
            self._keys[agent_id] = secrets.token_bytes(32)
        return self._keys[agent_id]

    def sign(
        self,
        *,
        agent_id: str,
        room_id: str,
        msg_type: str,
        payload: dict[str, Any],
    ) -> SignedMessage:
        key = self.ensure_key(agent_id)
        ts = time.time()
        nonce = secrets.token_hex(8)
        body = _canonical(agent_id, room_id, msg_type, payload, ts, nonce)
        sig = hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()
        return SignedMessage(agent_id, room_id, msg_type, payload, ts, nonce, sig)

    def verify(self, msg: SignedMessage, *, expected_agent_id: Optional[str] = None) -> tuple[bool, str]:
        if expected_agent_id and msg.agent_id != expected_agent_id:
            return False, "agent_id 与期望不符"
        key = self._keys.get(msg.agent_id)
        if not key:
            return False, "未知 Agent 密钥（可能伪造）"
        body = _canonical(
            msg.agent_id, msg.room_id, msg.msg_type, msg.payload, msg.ts, msg.nonce
        )
        expect = hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, msg.signature):
            return False, "签名校验失败（伪造或篡改）"
        return True, "ok"


def _canonical(
    agent_id: str,
    room_id: str,
    msg_type: str,
    payload: dict[str, Any],
    ts: float,
    nonce: str,
) -> str:
    return json.dumps(
        {
            "agent_id": agent_id,
            "room_id": room_id,
            "msg_type": msg_type,
            "payload": payload,
            "ts": ts,
            "nonce": nonce,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
