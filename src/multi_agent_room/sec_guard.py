"""T-SEC：安全策略门禁（Key 隔离 / 高危确认 / 沙盒分离）。

SecretStore 唯一实现见 secret_store.py；本模块不另建密钥存储。
"""

from __future__ import annotations

import re
from typing import Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.redact import redact_secrets
from multi_agent_room.secret_store import SecretStore, get_secret_store

# 疑似 API Key / Bearer 明文
_SECRET_PATTERNS = (
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*([^\s,;\"']+)"),
    re.compile(r"(?i)\bBearer\s+([A-Za-z0-9_\-\.]{8,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{12,})\b"),
    re.compile(r"(?i)\b(xox[baprs]-[A-Za-z0-9\-]{10,})\b"),
)


class SecViolation(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}:{message}")
        self.code = code
        self.message = message


def looks_like_secret(text: str) -> bool:
    if not text:
        return False
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def find_secret_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pat in _SECRET_PATTERNS:
        for m in pat.finditer(text or ""):
            hits.append(m.group(0)[:48])
    return hits


def assert_no_secret_in_text(text: str, *, where: str = "content") -> None:
    """SEC-02：共享稿 / 记忆不得含 Key 明文。"""
    hits = find_secret_hits(text)
    if hits:
        log_event("sec_deny", f"{where} 含疑似密钥明文（已拦截）")
        raise SecViolation(
            "secret_in_content",
            f"禁止将 API Key 明文写入{where}",
        )


def strip_secrets_for_prompt(text: str) -> str:
    """SEC-03：提示组装前剥离。"""
    return redact_secrets(text or "")


def assert_unique_secret_store(other: Optional[SecretStore] = None) -> SecretStore:
    """SEC-D：全应用唯一 SecretStore 单例。"""
    store = get_secret_store()
    if other is not None and other is not store:
        raise SecViolation(
            "duplicate_secret_store",
            "禁止第二套 SecretStore；M1 必须引用 get_secret_store()",
        )
    # 类型唯一：仅允许本模块实现类
    if type(store) is not SecretStore:
        raise SecViolation(
            "duplicate_secret_store",
            f"非法 SecretStore 子类/替身: {type(store)}",
        )
    return store


def assert_high_risk_confirmed(user_confirmed: bool) -> None:
    """SEC-04：高危写须用户确认。"""
    if not user_confirmed:
        raise SecViolation("need_confirm", "高危写未确认，不执行")


def assert_sandbox_not_workspace(
    sandbox_dir: str, workspace_path: Optional[str]
) -> None:
    """SEC-05：沙盒临时目录不得等于正式工作区，也不得位于工作区内。"""
    if not workspace_path:
        return
    from multi_agent_room.paths import normalize_workspace_path

    sb = normalize_workspace_path(sandbox_dir)
    ws = normalize_workspace_path(workspace_path)
    if sb == ws:
        raise SecViolation(
            "sandbox_workspace_overlap",
            "沙盒目录不可与正式工作区相同",
        )
    try:
        sb.relative_to(ws)
    except ValueError:
        return
    raise SecViolation(
        "sandbox_workspace_overlap",
        "沙盒不得位于正式工作区内",
    )
