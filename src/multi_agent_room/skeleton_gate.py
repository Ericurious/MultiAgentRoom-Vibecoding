"""T-M11-04：骨架分段配额 — 编排器强制拒收超长（默认非仅警告）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from multi_agent_room.logging_setup import log_event

DEFAULT_MAX_LINES = 400
DEFAULT_MAX_CHARS = 10_000


@dataclass
class SkeletonQuota:
    max_lines: int = DEFAULT_MAX_LINES
    max_chars: int = DEFAULT_MAX_CHARS
    warn_only: bool = False  # 默认强制拒收


@dataclass
class SkeletonCheck:
    ok: bool
    message: str
    code: str = ""
    lines: int = 0
    chars: int = 0
    warn_only: bool = False

    @property
    def rejected(self) -> bool:
        return (not self.ok) and (not self.warn_only)


def check_skeleton(
    text: str,
    *,
    quota: Optional[SkeletonQuota] = None,
    label: str = "fragment",
) -> SkeletonCheck:
    """单文件/单块行数或字数超配额 → 拒收，要求拆段。"""
    q = quota or SkeletonQuota()
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    if not text:
        lines = 0
    chars = len(text)
    over_lines = lines > q.max_lines
    over_chars = chars > q.max_chars
    if not over_lines and not over_chars:
        return SkeletonCheck(ok=True, message="ok", lines=lines, chars=chars)

    detail = (
        f"{label} 超骨架配额：lines={lines}/{q.max_lines} chars={chars}/{q.max_chars}；"
        "请拆成多段 PATCH/多文件后再交"
    )
    log_event(
        "skeleton_reject" if not q.warn_only else "skeleton_warn",
        detail,
    )
    return SkeletonCheck(
        ok=False,
        message=detail,
        code="skeleton_overflow",
        lines=lines,
        chars=chars,
        warn_only=q.warn_only,
    )
