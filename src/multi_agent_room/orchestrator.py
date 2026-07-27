"""M8a：编排器 — phase / frozen / 预算 / 升用户钩子（T-M8a-01～04）。

约定：
- `room.frozen` / `room.phase` / `room.clarify_hold` 的**写入**只经本编排器；
- M2 仅发 Interrupt / Resume / Clarify 命令，不另维护第二份 Frozen。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from multi_agent_room.budget import Budget, BudgetConfig
from multi_agent_room.logging_setup import log_event, log_phase_change
from multi_agent_room.phase_machine import (
    ALL_PHASES,
    CLARIFIABLE,
    DEFAULT_AGENDA,
    FREEZABLE,
    agenda_contains_review_judge_confirm,
    can_transition,
)
from multi_agent_room.room import Room


EscalationHandler = Callable[[str, str], None]  # (room_id, reason)


@dataclass
class OrchRoomState:
    """编排器侧房间运行态（与 Room 实体共享 frozen/phase 字段引用）。"""

    room: Room
    agenda: list[str] = field(default_factory=lambda: list(DEFAULT_AGENDA))
    budget: Budget = field(default_factory=lambda: Budget(BudgetConfig()))
    updated_at: float = field(default_factory=time.time)
    last_reject_message: str = ""
    escalation_hint: str = ""


class Orchestrator:
    """唯一写 frozen；推进 phase；预算与升用户。"""

    def __init__(self, *, budget_config: Optional[BudgetConfig] = None) -> None:
        self._budget_cfg = budget_config or BudgetConfig()
        self._states: dict[str, OrchRoomState] = {}
        self._on_escalation: list[EscalationHandler] = []

    def on_escalation(self, handler: EscalationHandler) -> None:
        self._on_escalation.append(handler)

    def bind(self, room: Room) -> OrchRoomState:
        st = OrchRoomState(
            room=room,
            agenda=list(DEFAULT_AGENDA),
            budget=Budget(BudgetConfig(
                max_patches_per_round=self._budget_cfg.max_patches_per_round,
                max_r2=self._budget_cfg.max_r2,
                max_confirm_churn=self._budget_cfg.max_confirm_churn,
            )),
        )
        self._states[room.room_id] = st
        # 确保新房议程合格
        assert agenda_contains_review_judge_confirm(st.agenda)
        log_event("orch_bind", f"room={room.room_id} agenda=default")
        return st

    def get(self, room_id: str) -> Optional[OrchRoomState]:
        return self._states.get(room_id)

    def require(self, room_id: str) -> OrchRoomState:
        st = self._states.get(room_id)
        if not st:
            raise KeyError(f"编排器未绑定房间: {room_id}")
        return st

    def ensure_bound(self, room: Room) -> OrchRoomState:
        existing = self._states.get(room.room_id)
        if existing:
            # 同一 Room 对象引用（M8a-C）
            existing.room = room
            return existing
        return self.bind(room)

    # ---- phase 迁移 ----
    def transition(
        self,
        room_id: str,
        to_phase: str,
        *,
        reason: str = "",
        force_special: bool = False,
    ) -> tuple[bool, str]:
        """尝试迁移；非法则拒绝并记 ENV-07a。"""
        st = self.require(room_id)
        room = st.room
        from_phase = room.phase
        if to_phase not in ALL_PHASES:
            msg = f"未知 phase: {to_phase}"
            self._reject(room_id, from_phase, to_phase, msg)
            return False, msg
        if room.frozen and to_phase != "Frozen" and not force_special:
            # Frozen 期间禁止普通迁移（Resume 走 force_special）
            msg = f"Frozen 中禁止迁移 {from_phase} → {to_phase}"
            self._reject(room_id, from_phase, to_phase, msg)
            return False, msg
        if not force_special and not can_transition(from_phase, to_phase):
            msg = f"非法 phase 迁移: {from_phase} → {to_phase}"
            self._reject(room_id, from_phase, to_phase, msg)
            return False, msg

        room.phase = to_phase  # type: ignore[assignment]
        st.updated_at = time.time()
        if from_phase != to_phase:
            log_phase_change(from_phase, to_phase, room_id=room_id)
            log_event(
                "phase_transition",
                f"{from_phase} -> {to_phase}" + (f" ({reason})" if reason else ""),
                room_id=room_id,
            )
        return True, "ok"

    def _reject(self, room_id: str, frm: str, to: str, msg: str) -> None:
        st = self.require(room_id)
        st.last_reject_message = msg
        log_event(
            "phase_reject",
            msg,
            room_id=room_id,
            level=__import__("logging").INFO,
        )
        # ENV-07a：显式事件名
        log_event("protocol_reject", f"illegal_phase {frm}->{to}: {msg}", room_id=room_id)

    # ---- Frozen 单一真相（T-M8a-03）----
    def interrupt(self, room_id: str) -> tuple[bool, str]:
        """M2 发 Interrupt；仅此写入 frozen=True。"""
        st = self.require(room_id)
        room = st.room
        if room.frozen:
            return True, "already frozen"
        if room.phase not in FREEZABLE and room.phase != "Frozen":
            # 仍允许从审阅相关态打断；其它态也可打断但保持 review 语义
            if room.phase in ("Idle", "Campaign", "AwaitingFirstAnswer", "Final"):
                return False, f"当前 phase={room.phase} 不可打断"
        room.phase_before_hold = room.phase
        room.frozen = True  # 唯一写入口之一
        if room.phase in FREEZABLE or room.review_window_open:
            room.review_window_open = True
        ok, msg = self.transition(room_id, "Frozen", reason="Interrupt", force_special=True)
        if not ok:
            room.frozen = False
            return False, msg
        log_event("interrupt", f"room={room_id} frozen=1")
        return True, "ok"

    def resume(self, room_id: str) -> tuple[bool, str]:
        """M2 发 Resume；仅此清除 frozen。"""
        st = self.require(room_id)
        room = st.room
        if not room.frozen and room.phase != "Frozen":
            return True, "not frozen"
        prev = room.phase_before_hold or "ReviewOpen"
        if prev not in ALL_PHASES or prev in ("Frozen", "AwaitingUserClarify"):
            prev = "ReviewOpen"
        room.frozen = False  # 唯一写入口之一
        room.phase_before_hold = None
        ok, msg = self.transition(
            room_id, prev, reason="Resume", force_special=True
        )
        if not ok:
            room.frozen = True
            return False, msg
        if prev in ("ReviewOpen", "ConfirmOpen", "AwaitingJudge"):
            room.review_window_open = True
        log_event("resume", f"room={room_id} phase={prev}")
        return True, "ok"

    def set_clarify_hold(self, room_id: str, question: str) -> tuple[bool, str]:
        st = self.require(room_id)
        room = st.room
        if room.phase not in CLARIFIABLE and room.phase != "AwaitingUserClarify":
            if room.phase == "Frozen":
                return False, "Frozen 中请先恢复再澄清"
            # 宽松：若已在 Review 族
            if room.phase not in CLARIFIABLE:
                return False, f"phase={room.phase} 不可澄清 Hold"
        if not room.clarify_hold:
            room.phase_before_hold = room.phase
        room.clarify_hold = True
        room.pending_clarify = question
        if room.review_window_open or room.phase in CLARIFIABLE:
            room.review_window_open = True
        return self.transition(
            room_id, "AwaitingUserClarify", reason="Clarify", force_special=True
        )

    def clear_clarify_hold(self, room_id: str) -> tuple[bool, str]:
        st = self.require(room_id)
        room = st.room
        if not room.clarify_hold and room.phase != "AwaitingUserClarify":
            return False, "当前无澄清 Hold"
        prev = room.phase_before_hold or "ReviewOpen"
        if prev in ("Frozen", "AwaitingUserClarify"):
            prev = "ReviewOpen"
        room.clarify_hold = False
        room.pending_clarify = None
        room.phase_before_hold = None
        ok, msg = self.transition(
            room_id, prev, reason="ClarifyAnswer", force_special=True
        )
        if ok and prev in ("ReviewOpen", "ConfirmOpen", "AwaitingJudge"):
            room.review_window_open = True
        return ok, msg

    # ---- 预算 / 升用户（T-M8a-02/04）----
    def record_patch(self, room_id: str) -> tuple[bool, str]:
        st = self.require(room_id)
        ok, msg = st.budget.record_patch()
        if not ok:
            self._escalate(room_id, msg)
        return ok, msg

    def record_r2(self, room_id: str) -> tuple[bool, str]:
        st = self.require(room_id)
        ok, msg = st.budget.record_r2()
        if ok:
            # 合法：评判后 R2 → 再首答
            self.transition(room_id, "AwaitingFirstAnswer", reason="R2")
        else:
            self._escalate(room_id, msg)
        return ok, msg

    def open_confirm(self, room_id: str) -> tuple[bool, str]:
        st = self.require(room_id)
        # 先进入 ConfirmOpen（若尚未）
        if st.room.phase == "AwaitingJudge":
            tok, tmsg = self.transition(room_id, "ConfirmOpen", reason="合入后确认")
            if not tok:
                return False, tmsg
        elif st.room.phase == "ConfirmOpen":
            # 再 +1 轮：ConfirmOpen → ConfirmOpen
            tok, tmsg = self.transition(room_id, "ConfirmOpen", reason="确认轮+1")
            if not tok:
                return False, tmsg
        ok, msg = st.budget.open_confirm_round()
        if not ok:
            self._escalate(room_id, msg)
            return False, msg
        return True, "ok"

    def _escalate(self, room_id: str, reason: str) -> None:
        st = self.require(room_id)
        st.escalation_hint = reason
        st.room.escalation_hint = reason
        st.budget.stopped = True
        st.budget.stop_reason = reason
        # 升用户 phase（若可）
        cur = st.room.phase
        if cur in ("ConfirmOpen", "ReviewOpen", "AwaitingJudge", "AwaitingUserEscalation"):
            if cur != "AwaitingUserEscalation":
                # ConfirmOpen → Escalation 合法；其它用 force
                force = cur != "ConfirmOpen"
                self.transition(
                    room_id,
                    "AwaitingUserEscalation",
                    reason=reason,
                    force_special=force,
                )
        log_event("user_escalation", reason, room_id=room_id)
        for h in self._on_escalation:
            h(room_id, reason)

    def user_escalation_choices(self, room_id: str) -> list[str]:
        return [
            "force_judge_approve",
            "r2_full_reject",
            "raise_confirm_cap",
            "end_without_final",
        ]

    def apply_escalation_choice(self, room_id: str, choice: str) -> tuple[bool, str]:
        st = self.require(room_id)
        if st.room.phase != "AwaitingUserEscalation":
            return False, "当前不在升用户态"
        if choice == "force_judge_approve":
            st.room.gate_passed = True
            st.budget.stopped = False
            return self.transition(room_id, "Final", reason="用户强制通过")
        if choice == "r2_full_reject":
            st.budget.stopped = False
            return self.transition(room_id, "AwaitingFirstAnswer", reason="用户选 R2")
        if choice == "raise_confirm_cap":
            st.budget.raise_confirm_cap(2)
            return self.transition(room_id, "ConfirmOpen", reason="用户提额")
        if choice == "end_without_final":
            st.room.gate_passed = False
            return self.transition(room_id, "Idle", reason="结束无 Final")
        return False, f"未知选项: {choice}"

    def frozen_shares_room_object(self, room: Room) -> bool:
        """M8a-C：编排器绑定的是同一 Room 实例（同一 frozen 字段）。"""
        st = self._states.get(room.room_id)
        return st is not None and st.room is room
