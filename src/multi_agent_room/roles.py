"""M3：职责认领、竞选窗、资格锁、降级 A（T-M3-04～07/09）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Optional

from multi_agent_room.logging_setup import log_event

RoleName = Literal["first_answerer", "reviewer", "judge"]


@dataclass
class RoleAssignment:
    first_answerer_agent_id: Optional[str] = None
    first_answerer_config_id: Optional[str] = None
    reviewer_agent_ids: list[str] = field(default_factory=list)
    judge_agent_id: Optional[str] = None  # None 且 judge_is_user=False 表示未定
    judge_is_user: bool = False
    frozen: bool = False

    def copy(self) -> "RoleAssignment":
        return RoleAssignment(
            first_answerer_agent_id=self.first_answerer_agent_id,
            first_answerer_config_id=self.first_answerer_config_id,
            reviewer_agent_ids=list(self.reviewer_agent_ids),
            judge_agent_id=self.judge_agent_id,
            judge_is_user=self.judge_is_user,
            frozen=self.frozen,
        )


@dataclass
class RoomRoundState:
    room_id: str
    user_question: Optional[str] = None
    campaign_open: bool = False
    campaign_deadline_ts: float = 0.0
    campaign_seconds: int = 60
    roles: RoleAssignment = field(default_factory=RoleAssignment)
    # 候选缓冲：职责未冻结时不得正式写 M4
    candidate_buffer: list[dict] = field(default_factory=list)
    invited_agent_ids: list[str] = field(default_factory=list)
    # agent_id -> model_config_id
    agent_config_map: dict[str, str] = field(default_factory=dict)
    ready_model_count: int = 0

    def phase_hint(self) -> str:
        if not self.user_question:
            return "Idle"
        if self.campaign_open and not self.roles.frozen:
            return "Campaign"
        if self.roles.frozen and not self.roles.first_answerer_agent_id:
            return "AwaitingFirstAnswer"
        if self.roles.frozen:
            return "RolesFrozen"
        return "Idle"


class RoleService:
    def __init__(self) -> None:
        self._rounds: dict[str, RoomRoundState] = {}

    def get_or_create(self, room_id: str) -> RoomRoundState:
        if room_id not in self._rounds:
            self._rounds[room_id] = RoomRoundState(room_id=room_id)
        return self._rounds[room_id]

    def invite(self, room_id: str, agent_id: str, model_config_id: str) -> None:
        st = self.get_or_create(room_id)
        if agent_id not in st.invited_agent_ids:
            st.invited_agent_ids.append(agent_id)
        st.agent_config_map[agent_id] = model_config_id

    def set_ready_model_count(self, room_id: str, n: int) -> None:
        self.get_or_create(room_id).ready_model_count = n

    def ask_question(self, room_id: str, question: str, *, open_campaign: bool = True) -> RoomRoundState:
        """用户提问后才可启动竞选（M3-I）。"""
        st = self.get_or_create(room_id)
        if not question.strip():
            raise ValueError("问题不能为空")
        st.user_question = question.strip()
        st.roles = RoleAssignment()
        st.candidate_buffer.clear()
        if open_campaign:
            st.campaign_open = True
            st.campaign_deadline_ts = time.time() + st.campaign_seconds
            log_event("campaign_open", f"room={room_id}")
        return st

    def start_campaign_without_question(self, room_id: str) -> tuple[bool, str]:
        st = self.get_or_create(room_id)
        if not st.user_question:
            return False, "未提问：房间空闲，不得启动竞选/首答"
        return True, "ok"

    def claim(
        self,
        room_id: str,
        agent_id: str,
        role: RoleName,
    ) -> tuple[bool, str]:
        st = self.get_or_create(room_id)
        if not st.user_question:
            return False, "未提问：不得认领职责"
        if st.roles.frozen:
            return False, "职责已冻结，不可自行改职"
        if not st.campaign_open:
            return False, "竞选窗未开启"
        if time.time() > st.campaign_deadline_ts:
            st.campaign_open = False
            return False, "竞选窗已超时关闭"

        cfg_id = st.agent_config_map.get(agent_id)
        if not cfg_id:
            return False, "Agent 未邀请入房或未绑定模型"

        if role == "judge":
            ok, reason = self._check_qualification_lock(st, agent_id, cfg_id)
            if not ok:
                return False, reason
            st.roles.judge_agent_id = agent_id
            st.roles.judge_is_user = False
            return True, "已认领评判"

        if role == "first_answerer":
            st.roles.first_answerer_agent_id = agent_id
            st.roles.first_answerer_config_id = cfg_id
            # 首答可自审：自动具备审阅权限
            if agent_id not in st.roles.reviewer_agent_ids:
                st.roles.reviewer_agent_ids.append(agent_id)
            return True, "已认领首答（并获得自审权限）"

        if role == "reviewer":
            if agent_id not in st.roles.reviewer_agent_ids:
                st.roles.reviewer_agent_ids.append(agent_id)
            return True, "已认领审阅"

        return False, f"未知职责: {role}"

    def user_assign(
        self,
        room_id: str,
        *,
        first_answerer_agent_id: Optional[str] = None,
        judge_agent_id: Optional[str] = None,
        judge_is_user: Optional[bool] = None,
        reviewer_agent_ids: Optional[list[str]] = None,
    ) -> tuple[bool, str]:
        """用户指定优先于竞选（M3-G）。"""
        st = self.get_or_create(room_id)
        if not st.user_question:
            return False, "未提问：不得指定职责"
        roles = st.roles.copy()

        if first_answerer_agent_id is not None:
            roles.first_answerer_agent_id = first_answerer_agent_id
            roles.first_answerer_config_id = st.agent_config_map.get(first_answerer_agent_id)
            if first_answerer_agent_id not in roles.reviewer_agent_ids:
                roles.reviewer_agent_ids.append(first_answerer_agent_id)

        if judge_is_user:
            roles.judge_is_user = True
            roles.judge_agent_id = None
        elif judge_agent_id is not None:
            cfg = st.agent_config_map.get(judge_agent_id)
            ok, reason = self._check_qualification_lock(st, judge_agent_id, cfg or "", provisional=roles)
            if not ok:
                return False, reason
            roles.judge_agent_id = judge_agent_id
            roles.judge_is_user = False

        if reviewer_agent_ids is not None:
            roles.reviewer_agent_ids = list(reviewer_agent_ids)

        roles.frozen = True
        st.roles = roles
        st.campaign_open = False
        self._apply_fallbacks(st)
        log_event("user_assign_roles", f"room={room_id}")
        return True, "用户指定已冻结职责"

    def freeze_campaign(self, room_id: str) -> tuple[bool, str]:
        st = self.get_or_create(room_id)
        if not st.user_question:
            return False, "未提问"
        st.campaign_open = False
        st.roles.frozen = True
        self._apply_fallbacks(st)
        # 降级 A
        if st.ready_model_count <= 1:
            st.roles.judge_is_user = True
            st.roles.judge_agent_id = None
            log_event("degrade_a", f"room={room_id}")
        return True, "竞选已冻结"

    def _apply_fallbacks(self, st: RoomRoundState) -> None:
        # 无人审阅 → 除评判外全体（含首答）
        if not st.roles.reviewer_agent_ids:
            judge = st.roles.judge_agent_id
            st.roles.reviewer_agent_ids = [
                a for a in st.invited_agent_ids if a != judge
            ]
        # 无人评判且非降级
        if (
            not st.roles.judge_is_user
            and not st.roles.judge_agent_id
            and st.ready_model_count > 1
        ):
            log_event("need_judge", f"room={st.room_id} 请指定非首答模型为评判")

    def _check_qualification_lock(
        self,
        st: RoomRoundState,
        judge_agent_id: str,
        judge_config_id: str,
        *,
        provisional: Optional[RoleAssignment] = None,
    ) -> tuple[bool, str]:
        roles = provisional or st.roles
        first_cfg = roles.first_answerer_config_id
        if first_cfg and judge_config_id and first_cfg == judge_config_id:
            return False, "资格锁：首答模型 configId 不得任本轮评判"
        # 同 agent 既首答又评判也不允许（即使不同配置，P0 按 agent 也拦一层更稳）
        if roles.first_answerer_agent_id and roles.first_answerer_agent_id == judge_agent_id:
            return False, "资格锁：首答 Agent 不得任本轮评判"
        return True, "ok"

    def can_write_shared_doc(self, room_id: str) -> tuple[bool, str]:
        """职责未冻结禁止正式写 M4。"""
        st = self.get_or_create(room_id)
        if not st.user_question:
            return False, "未提问"
        if not st.roles.frozen:
            return False, "职责未冻结：生成只能进私有思考/候选缓冲"
        return True, "ok"

    def buffer_candidate(self, room_id: str, agent_id: str, text: str) -> None:
        st = self.get_or_create(room_id)
        st.candidate_buffer.append({"agent_id": agent_id, "text": text})

    def effective_reviewers(self, room_id: str) -> list[str]:
        st = self.get_or_create(room_id)
        if st.roles.reviewer_agent_ids:
            return list(st.roles.reviewer_agent_ids)
        judge = st.roles.judge_agent_id
        return [a for a in st.invited_agent_ids if a != judge]

    def worker_agent_ids(self, room_id: str) -> list[str]:
        """非评议 Agent = 工人。"""
        st = self.get_or_create(room_id)
        if st.roles.judge_is_user:
            return list(st.invited_agent_ids)
        return [a for a in st.invited_agent_ids if a != st.roles.judge_agent_id]

    def can_judge_approve(self, room_id: str, agent_id: Optional[str]) -> tuple[bool, str]:
        st = self.get_or_create(room_id)
        if st.roles.judge_is_user:
            if agent_id is None:
                return True, "用户代评议"
            return False, "降级 A：仅用户可 JudgeApprove"
        if not st.roles.judge_agent_id:
            return False, "尚未指定评判"
        if agent_id != st.roles.judge_agent_id:
            return False, "工人/非评议不得 JudgeApprove"
        return True, "ok"

    def can_emit_patch(self, room_id: str, agent_id: str) -> tuple[bool, str]:
        st = self.get_or_create(room_id)
        reviewers = self.effective_reviewers(room_id)
        if agent_id in reviewers or agent_id == st.roles.first_answerer_agent_id:
            return True, "ok"
        if agent_id in self.worker_agent_ids(room_id):
            return True, "工人可发实质 PATCH"
        return False, "无审阅权限"
