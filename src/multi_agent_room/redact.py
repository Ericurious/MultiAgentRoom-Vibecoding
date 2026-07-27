"""密钥脱敏（T-M10-08）：提示词/日志不得含 API Key 明文。"""

from __future__ import annotations

import re
from typing import Any

# 对齐 logging_setup；额外覆盖常见 sk- 形态
_SECRET_KV = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|secret|password)\s*[:=]\s*([^\s,;\"']+)"
)
_SK_TOKEN = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b")
_BEARER = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9_\-\.]+)")


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = _SECRET_KV.sub(r"\1=***", text)
    out = _BEARER.sub("Bearer ***", out)
    out = _SK_TOKEN.sub("***", out)
    return out


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对 prompt 消息内容脱敏（不改结构）。"""
    cleaned: list[dict[str, Any]] = []
    for m in messages:
        item = dict(m)
        if isinstance(item.get("content"), str):
            item["content"] = redact_secrets(item["content"])
        cleaned.append(item)
    return cleaned


def assert_no_secret_plaintext(blob: str, *, known_secrets: list[str] | None = None) -> None:
    """红线断言：已知密钥不得出现在文本中。"""
    for s in known_secrets or []:
        if s and s in blob:
            raise AssertionError("红线失败：检测到 API Key 明文")
    if _SK_TOKEN.search(blob) and "sk-" in blob and "***" not in blob[blob.find("sk-") : blob.find("sk-") + 20]:
        # 宽松：若仍有未脱敏 sk- 长串则失败
        m = _SK_TOKEN.search(blob)
        if m and m.group(1) not in ("***",):
            raise AssertionError(f"红线失败：疑似 Key 明文 {m.group(1)[:12]}…")
