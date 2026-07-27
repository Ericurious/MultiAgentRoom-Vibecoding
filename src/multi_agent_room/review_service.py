"""T-M5：审阅服务门面 — 窗 + 过滤 + 队列 + MarkTrivial。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.patch_filter import (
    SIX_DIM_HINT,
    FilterResult,
    PatchFilter,
    PatchItem,
    PatchQueue,
)
from multi_agent_room.review_window import ReviewWindow


@dataclass
class CloseResult:
    closed: bool
    reason: str
    queue: list[PatchItem]
    auto_final: bool = False  # M5 永不为 True


class ReviewService:
    def __init__(
        self,
        *,
        quiet_period_ms: int = 15000,
        timeout_ms: int = 120000,
    ) -> None:
        self.quiet_period_ms = quiet_period_ms
        self.timeout_ms = timeout_ms
        self.filter = PatchFilter()
        self.queue = PatchQueue()
        self._windows: dict[str, ReviewWindow] = {}
        self._changed_sets: dict[str, Optional[set[str]]] = {}
        self._skip_rewrite: dict[str, bool] = {}

    def six_dim_hint(self) -> str:
        return SIX_DIM_HINT

    def open_window(
        self,
        room_id: str,
        version: int,
        *,
        now: Optional[float] = None,
        quiet_period_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
    ) -> ReviewWindow:
        now = time.time() if now is None else now
        win = ReviewWindow(
            room_id=room_id,
            version=version,
            opened_at=now,
            quiet_period_ms=quiet_period_ms or self.quiet_period_ms,
            timeout_ms=timeout_ms or self.timeout_ms,
        )
        self._windows[room_id] = win
        log_event(
            "review_open",
            f"V{version} quiet={win.quiet_period_ms}ms timeout={win.timeout_ms}ms",
            room_id=room_id,
        )
        return win

    def get_window(self, room_id: str) -> Optional[ReviewWindow]:
        return self._windows.get(room_id)

    def set_changed_set(self, room_id: str, targets: Optional[set[str]]) -> None:
        self._changed_sets[room_id] = targets

    def allow_r2_rewrite_bypass(self, room_id: str, enabled: bool = True) -> None:
        """R2 后新首答写入口：不误杀全文重写。"""
        self._skip_rewrite[room_id] = enabled
        if enabled:
            self.filter.grant_rewrite(room_id, 0)  # ensure map exists
        log_event("rewrite_bypass", f"enabled={enabled}", room_id=room_id)

    def grant_rewrite_token(self, room_id: str, n: int = 1) -> None:
        self.filter.grant_rewrite(room_id, n)

    def register_read(self, room_id: str, agent_id: str) -> None:
        win = self._windows.get(room_id)
        if win:
            win.mark_read(agent_id)

    def register_abstain(self, room_id: str, agent_id: str) -> None:
        win = self._windows.get(room_id)
        if win:
            win.mark_abstain(agent_id)

    def sync_pause(
        self,
        room_id: str,
        *,
        frozen: bool,
        clarify_hold: bool,
        now: Optional[float] = None,
    ) -> None:
        win = self._windows.get(room_id)
        if not win:
            return
        win.set_frozen(frozen, now=now)
        win.set_clarify_hold(clarify_hold, now=now)

    def submit_patch(
        self,
        *,
        room_id: str,
        agent_id: str,
        fields: dict[str, Any],
        old_text: str,
        doc_full: str,
        doc_version: int,
        other_block_texts: Optional[dict[str, str]] = None,
        skip_changed_set: bool = False,
    ) -> FilterResult:
        allowed = None if skip_changed_set else self._changed_sets.get(room_id)
        result = self.filter.filter(
            room_id=room_id,
            agent_id=agent_id,
            fields=fields,
            old_text=old_text,
            doc_full=doc_full,
            doc_version=doc_version,
            other_block_texts=other_block_texts,
            allowed_targets=allowed,
            skip_rewrite_check=bool(self._skip_rewrite.get(room_id)),
        )
        if not result.ok or not result.patch:
            log_event(
                "patch_reject",
                f"code={result.code} {result.message}",
                room_id=room_id,
            )
            return result
        self.queue.enqueue(result.patch)
        win = self._windows.get(room_id)
        if win:
            win.note_valid_patch()
            win.mark_read(agent_id)  # 发贴者记已读
        # 入队后关闭 R2 bypass（仅保护新首答入口瞬间）
        self._skip_rewrite[room_id] = False
        log_event(
            "patch_queued",
            f"id={result.patch.patch_id} target={result.patch.target}",
            room_id=room_id,
        )
        return result

    def mark_trivial(
        self,
        room_id: str,
        patch_id: str,
        *,
        by_agent_id: Optional[str],
        judge_allowed: bool,
    ) -> tuple[bool, str]:
        if not judge_allowed:
            return False, "MarkTrivial 仅评议/用户；工人失败"
        ok = self.queue.mark_trivial(room_id, patch_id)
        if not ok:
            return False, "补丁不在队列"
        log_event(
            "mark_trivial",
            f"patch={patch_id} by={by_agent_id or 'user'}",
            room_id=room_id,
        )
        return True, "ok"

    def pending(self, room_id: str) -> list[PatchItem]:
        return self.queue.list(room_id)

    def pending_as_dicts(self, room_id: str) -> list[dict[str, Any]]:
        return [
            {
                "patch_id": p.patch_id,
                "target": p.target,
                "category": p.category,
                "claim": p.claim,
                "replace": p.replace,
                "version": p.version,
                "agent_id": p.agent_id,
            }
            for p in self.pending(room_id)
        ]

    def try_close(
        self,
        room_id: str,
        effective_reviewers: list[str],
        *,
        force: bool = False,
        now: Optional[float] = None,
    ) -> CloseResult:
        win = self._windows.get(room_id)
        if not win:
            return CloseResult(False, "no_window", [])
        q = self.queue.list(room_id)
        ok, reason = win.can_close(
            effective_reviewers, queue_len=len(q), force=force, now=now
        )
        if not ok:
            return CloseResult(False, reason, q, auto_final=False)
        win.close(reason)
        # 交给 M6：队列保留，不写最终回复
        log_event("review_close", f"reason={reason} queue={len(q)}", room_id=room_id)
        return CloseResult(True, reason, q, auto_final=False)

    def silent_agree_map(
        self, room_id: str, agent_ids: list[str]
    ) -> dict[str, bool]:
        win = self._windows.get(room_id)
        empty = len(self.queue.list(room_id)) == 0
        if not win:
            return {a: False for a in agent_ids}
        return {
            a: win.has_silent_agree(a, queue_empty=empty) for a in agent_ids
        }
