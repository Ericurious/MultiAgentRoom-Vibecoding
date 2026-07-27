"""M3 门面服务：身份 / 会话 / 职责 / 签名 / 私有思考。"""

from __future__ import annotations

from typing import Any, Optional

from multi_agent_room.agent_profile import (
    AgentProfile,
    AgentProfileStore,
    new_agent_id,
)
from multi_agent_room.identity import IdentityVault, SignedMessage
from multi_agent_room.logging_setup import log_event
from multi_agent_room.model_service import ModelService
from multi_agent_room.private_thought import PrivateThoughtStore
from multi_agent_room.roles import RoleName, RoleService, RoomRoundState
from multi_agent_room.session import SessionRegistry


class AgentService:
    def __init__(
        self,
        profiles: Optional[AgentProfileStore] = None,
        models: Optional[ModelService] = None,
    ) -> None:
        self.profiles = profiles or AgentProfileStore()
        self.models = models or ModelService()
        self.sessions = SessionRegistry()
        self.roles = RoleService()
        self.identity = IdentityVault()
        self.thoughts = PrivateThoughtStore()

    # ---- 身份 ----
    def create_agent(
        self,
        *,
        display_name: str,
        model_config_id: str,
        agent_id: Optional[str] = None,
        persona: str = "",
        backup_config_ids: Optional[list[str]] = None,
        capability_tags: Optional[list[str]] = None,
    ) -> AgentProfile:
        # 绑定门禁：建议模型至少存在；ready 在入房大脑时再严检
        if not self.models.store.get(model_config_id):
            raise KeyError(f"模型不存在: {model_config_id}")
        profile = AgentProfile(
            agent_id=agent_id or new_agent_id(),
            display_name=display_name.strip() or "Agent",
            model_config_id=model_config_id,
            persona=persona,
            backup_config_ids=list(backup_config_ids or []),
            capability_tags=list(capability_tags or []),
        )
        self.profiles.upsert(profile)
        self.identity.ensure_key(profile.agent_id)
        log_event("agent_create", f"id={profile.agent_id} model={model_config_id}")
        return profile

    def list_agents(self) -> list[AgentProfile]:
        return self.profiles.list_all()

    def delete_agent(self, agent_id: str) -> None:
        if not self.profiles.get(agent_id):
            raise KeyError(f"Agent 不存在: {agent_id}")
        self.profiles.delete(agent_id)
        log_event("agent_delete", f"id={agent_id}")

    # ---- 入房 / 提问 / 竞选 ----
    def invite_to_room(self, room_id: str, agent_id: str) -> None:
        p = self.profiles.get(agent_id)
        if not p:
            raise KeyError(f"Agent 不存在: {agent_id}")
        self.roles.invite(room_id, agent_id, p.model_config_id)
        self.sessions.open(agent_id, room_id)
        # ready 模型计数：基于当前可绑定模型数近似
        self.roles.set_ready_model_count(room_id, len(self.models.list_bindable()) or 1)

    def user_ask(self, room_id: str, question: str) -> RoomRoundState:
        return self.roles.ask_question(room_id, question)

    def claim_role(self, room_id: str, agent_id: str, role: RoleName) -> tuple[bool, str]:
        return self.roles.claim(room_id, agent_id, role)

    def user_assign_roles(self, room_id: str, **kwargs: Any) -> tuple[bool, str]:
        return self.roles.user_assign(room_id, **kwargs)

    def freeze_roles(self, room_id: str) -> tuple[bool, str]:
        # 冻结前按入房 agent 的模型 ready 数更准确
        st = self.roles.get_or_create(room_id)
        ready_cfgs = {m.config_id for m in self.models.list_bindable()}
        n = len({st.agent_config_map.get(a) for a in st.invited_agent_ids if st.agent_config_map.get(a) in ready_cfgs})
        # 若尚无 ready（测试未探活），按不同 configId 数量计
        if n == 0:
            n = len({st.agent_config_map.get(a) for a in st.invited_agent_ids})
        self.roles.set_ready_model_count(room_id, n)
        return self.roles.freeze_campaign(room_id)

    # ---- 消息 ----
    def sign_message(
        self,
        *,
        agent_id: str,
        room_id: str,
        msg_type: str,
        payload: dict[str, Any],
    ) -> SignedMessage:
        return self.identity.sign(
            agent_id=agent_id,
            room_id=room_id,
            msg_type=msg_type,
            payload=payload,
        )

    def accept_message(self, msg: SignedMessage, *, as_agent: Optional[str] = None) -> tuple[bool, str]:
        """校验签名；并按类型做权限门禁。"""
        ok, reason = self.identity.verify(msg, expected_agent_id=as_agent or msg.agent_id)
        if not ok:
            return False, reason
        # 伪造场景：调用方声明是 A，但消息声称 B 且无 B 密钥 → verify 已失败
        if msg.msg_type == "JudgeApprove":
            return self.roles.can_judge_approve(msg.room_id, msg.agent_id)
        if msg.msg_type in ("Patch", "Read"):
            return self.roles.can_emit_patch(msg.room_id, msg.agent_id)
        return True, "ok"

    def forge_and_submit(
        self,
        *,
        pretend_agent_id: str,
        real_signer_agent_id: str,
        room_id: str,
        msg_type: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        """测试辅助：用 B 的密钥签发却声称 agent=A → 应失败。"""
        # 错误地用 real 的 key 但改 agent_id 字段
        msg = self.identity.sign(
            agent_id=real_signer_agent_id,
            room_id=room_id,
            msg_type=msg_type,
            payload=payload,
        )
        forged = SignedMessage(
            agent_id=pretend_agent_id,
            room_id=msg.room_id,
            msg_type=msg.msg_type,
            payload=msg.payload,
            ts=msg.ts,
            nonce=msg.nonce,
            signature=msg.signature,
        )
        return self.accept_message(forged)

    # ---- 私有思考 ----
    def think(self, agent_id: str, text: str) -> None:
        self.thoughts.write(agent_id, text)

    def context_for(self, viewer_agent_id: str, room_id: str) -> dict[str, Any]:
        st = self.roles.get_or_create(room_id)
        return self.thoughts.assemble_context_for(viewer_agent_id, st.invited_agent_ids)
