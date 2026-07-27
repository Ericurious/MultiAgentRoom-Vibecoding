"""T-M5：审阅窗状态机（静默期 / 超时 / Frozen / ClarifyHold）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReviewWindow:
    room_id: str
    version: int
    opened_at: float
    quiet_period_ms: int = 15000
    timeout_ms: int = 120000
    frozen: bool = False
    clarify_hold: bool = False
    readers: set[str] = field(default_factory=set)
    abstainers: set[str] = field(default_factory=set)
    last_valid_patch_at: Optional[float] = None
    closed: bool = False
    close_reason: str = ""
    handed_to_m6: bool = False
    # 暂停时累计已流逝的 wall 时间，恢复后用 remaining
    _pause_started_at: Optional[float] = None
    _elapsed_before_pause: float = 0.0
    _timeout_deadline: float = 0.0

    def __post_init__(self) -> None:
        if not self._timeout_deadline:
            self._timeout_deadline = self.opened_at + self.timeout_ms / 1000.0

    def mark_read(self, agent_id: str) -> None:
        if self.closed:
            return
        self.readers.add(agent_id)
        self.abstainers.discard(agent_id)

    def mark_abstain(self, agent_id: str) -> None:
        if self.closed:
            return
        self.abstainers.add(agent_id)
        # 弃权不算已读同意
        self.readers.discard(agent_id)

    def note_valid_patch(self, *, now: Optional[float] = None) -> None:
        if self.closed:
            return
        self.last_valid_patch_at = time.time() if now is None else now

    def set_frozen(self, value: bool, *, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if value and not self.frozen:
            self._begin_pause(now)
            self.frozen = True
        elif not value and self.frozen:
            self.frozen = False
            if not self.clarify_hold:
                self._end_pause(now)

    def set_clarify_hold(self, value: bool, *, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if value and not self.clarify_hold:
            self._begin_pause(now)
            self.clarify_hold = True
        elif not value and self.clarify_hold:
            self.clarify_hold = False
            if not self.frozen:
                self._end_pause(now)

    def _begin_pause(self, now: float) -> None:
        if self._pause_started_at is not None:
            return
        self._pause_started_at = now
        self._elapsed_before_pause = max(
            0.0, now - self.opened_at - self._idle_paused_total()
        )
        # store remaining timeout into deadline relative resume
        remaining = max(0.0, self._timeout_deadline - now)
        self._timeout_deadline = now + remaining  # placeholder until end
        self._paused_remaining = remaining

    def _idle_paused_total(self) -> float:
        return getattr(self, "_paused_accum", 0.0)

    def _end_pause(self, now: float) -> None:
        if self._pause_started_at is None:
            return
        paused = now - self._pause_started_at
        self._paused_accum = self._idle_paused_total() + paused
        rem = getattr(self, "_paused_remaining", max(0.0, self._timeout_deadline - now))
        self._timeout_deadline = now + rem
        self._pause_started_at = None

    def is_paused(self) -> bool:
        return bool(self.frozen or self.clarify_hold)

    def effective_reviewers(
        self, candidates: list[str], *, include_user_fallback: bool = False
    ) -> list[str]:
        """显式列表去掉弃权；若空且 allow fallback 则返回 ['__user__']。"""
        out = [a for a in candidates if a not in self.abstainers]
        if not out and include_user_fallback:
            return ["__user__"]
        return out

    def has_silent_agree(self, agent_id: str, *, queue_empty: bool) -> bool:
        """同意 = 已读 ∧ 本轮无有效补丁；弃权/未读 ≠ 同意。"""
        if agent_id in self.abstainers:
            return False
        if agent_id not in self.readers:
            return False
        return bool(queue_empty)

    def quiet_elapsed(self, *, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        if self.is_paused():
            return False
        if self.last_valid_patch_at is None:
            # 无补丁：自开窗起算静默期
            ref = self.opened_at
        else:
            ref = self.last_valid_patch_at
        return (now - ref) * 1000 >= self.quiet_period_ms

    def timed_out(self, *, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        if self.is_paused():
            return False
        return now >= self._timeout_deadline

    def can_close(
        self,
        effective: list[str],
        *,
        queue_len: int,
        force: bool = False,
        now: Optional[float] = None,
    ) -> tuple[bool, str]:
        """关闭条件（最早）：全员已读+quiet / 超时且≥1已读 / 强制。不结算假沉默。"""
        if self.closed:
            return True, self.close_reason or "already_closed"
        if self.is_paused():
            return False, "paused"
        now = time.time() if now is None else now
        if force:
            return True, "force_judge"
        eff = [a for a in effective if a not in self.abstainers]
        if (
            eff
            and all(a in self.readers for a in eff)
            and self.quiet_elapsed(now=now)
        ):
            return True, "all_read_quiet"
        if self.timed_out(now=now) and len(self.readers) >= 1:
            return True, "timeout_with_read"
        return False, "open"

    def close(self, reason: str) -> None:
        self.closed = True
        self.close_reason = reason
        self.handed_to_m6 = True
