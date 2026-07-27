"""T-M7：确认轮状态、ChangedSet 范围、JudgeApprove / CommitFinal 门禁。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from multi_agent_room.logging_setup import log_event

# T-M7-07：禁止配置跳过确认轮（硬编码）
ALLOW_SKIP_CONFIRM: bool = False


@dataclass
class ConfirmRound:
    room_id: str
    active: bool = False
    changed_set: set[str] = field(default_factory=set)
    confirm_index: int = 0
    max_confirm_churn: int = 3
    # 本轮是否发生过合入/合并/R3（路径·补丁确认）
    had_write: bool = False
    # 确认轮是否已「干净」结算（已读齐且无有效补丁）
    marked_clean: bool = False
    readers: set[str] = field(default_factory=set)


class ConfirmService:
    def __init__(self, *, max_confirm_churn: int = 3) -> None:
        self.max_confirm_churn = max_confirm_churn
        self._rounds: dict[str, ConfirmRound] = {}

    def get(self, room_id: str) -> ConfirmRound:
        if room_id not in self._rounds:
            self._rounds[room_id] = ConfirmRound(
                room_id=room_id, max_confirm_churn=self.max_confirm_churn
            )
        return self._rounds[room_id]

    def reset(self, room_id: str) -> None:
        """R2 / 新提问：确认状态清零。"""
        self._rounds[room_id] = ConfirmRound(
            room_id=room_id, max_confirm_churn=self.max_confirm_churn
        )
        log_event("confirm_reset", "cleared", room_id=room_id)

    def note_write(self, room_id: str, changed: list[str] | set[str]) -> None:
        st = self.get(room_id)
        st.had_write = True
        st.marked_clean = False
        st.changed_set = set(changed)
        st.readers.clear()

    def open_round(
        self, room_id: str, changed: list[str] | set[str], *, index: int
    ) -> tuple[bool, str]:
        """打开/再开确认轮；index 由预算层传入。封顶时返回 False。"""
        if ALLOW_SKIP_CONFIRM:
            raise RuntimeError("禁止跳过确认轮：ALLOW_SKIP_CONFIRM 必须为 False")
        st = self.get(room_id)
        st.had_write = True
        st.active = True
        st.marked_clean = False
        st.changed_set = set(changed)
        st.confirm_index = index
        st.readers.clear()
        if index > st.max_confirm_churn:
            st.active = True  # 仍标记需要确认，但应升用户
            return False, (
                f"确认轮已封顶：confirmIndex={index}/{st.max_confirm_churn}"
            )
        log_event(
            "confirm_open",
            f"index={index} changed={sorted(st.changed_set)}",
            room_id=room_id,
        )
        return True, "ok"

    def raise_cap(self, room_id: str, extra: int = 2) -> None:
        st = self.get(room_id)
        st.max_confirm_churn += max(1, extra)

    def mark_read(self, room_id: str, agent_id: str) -> None:
        st = self.get(room_id)
        if st.active:
            st.readers.add(agent_id)

    def changed_set(self, room_id: str) -> set[str]:
        return set(self.get(room_id).changed_set)

    def assert_patch_in_scope(self, room_id: str, target: str) -> tuple[bool, str]:
        st = self.get(room_id)
        if not st.active:
            return True, "ok"
        if not st.changed_set:
            return False, "ChangedSet 为空：确认轮拒收 PATCH"
        if target not in st.changed_set:
            return (
                False,
                f"超范围拒收: {target} 不在 ChangedSet={sorted(st.changed_set)}",
            )
        return True, "ok"

    def is_clean(
        self,
        room_id: str,
        *,
        effective_reviewers: list[str],
        pending_count: int,
    ) -> bool:
        st = self.get(room_id)
        if not st.active:
            return True
        if pending_count > 0:
            return False
        eff = [a for a in effective_reviewers if a != "__user__"]
        if not eff:
            # 空审阅者：用户已读即可
            return "__user__" in st.readers or bool(st.readers)
        return all(a in st.readers for a in eff)

    def mark_clean(self, room_id: str) -> None:
        st = self.get(room_id)
        st.marked_clean = True
        log_event("confirm_clean", f"index={st.confirm_index}", room_id=room_id)

    def can_judge_approve(
        self,
        room_id: str,
        *,
        phase: str,
        effective_reviewers: list[str],
        pending_count: int,
        force_skip_confirm: bool = False,
    ) -> tuple[bool, str]:
        """
        路径·静默通过：无合入 → 可直接 JudgeApprove。
        路径·补丁确认：须确认轮干净；禁止跳过。
        """
        if force_skip_confirm or ALLOW_SKIP_CONFIRM:
            return False, "禁止跳过确认轮（T-M7-07）"
        if pending_count > 0:
            return False, "待合入队列非空：不能 JudgeApprove"
        st = self.get(room_id)
        if not st.had_write:
            # 路径·静默通过：无合入则不开确认轮
            if st.active and phase == "ConfirmOpen":
                return False, "异常：无合入却处于确认轮"
            return True, "silent_path"
        # 路径·补丁确认：必须仍处确认轮且已干净
        if not st.active:
            return False, "有合入却无确认轮：不能 JudgeApprove（M7-A）"
        if not self.is_clean(
            room_id,
            effective_reviewers=effective_reviewers,
            pending_count=pending_count,
        ):
            return False, "确认轮未干净：须已读齐且无有效补丁"
        return True, "confirm_path_clean"

    def after_judge_approve(self, room_id: str) -> None:
        st = self.get(room_id)
        st.active = False
