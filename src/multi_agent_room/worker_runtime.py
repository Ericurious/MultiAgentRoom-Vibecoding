"""T-AGENT：工人 / 评议运行时与 Prompt 编排（§5.7）。

未提问不调用 W1–W4/J1；工人与评议上下文隔离；输出经 PROTO 闸门。
P0 默认单工人。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.prompts import (
    build_j1_prompt,
    build_w1_prompt,
    build_w2_prompt,
    build_w3_prompt,
    build_w4_prompt,
    prompt_contains_private_leak,
)
from multi_agent_room.protocol import NormalizeResult
from multi_agent_room.redact import sanitize_messages
from multi_agent_room.room_service import RoomService

MessageList = list[dict[str, str]]
ModelCaller = Callable[[MessageList, str], str]  # (messages, agent_id) -> text


class StepError(Exception):
    pass


@dataclass
class StepResult:
    ok: bool
    step: str
    agent_id: str
    prompt: MessageList = field(default_factory=list)
    raw_output: str = ""
    proto: Optional[NormalizeResult] = None
    error: str = ""
    model_called: bool = False


@dataclass
class AgentRuntime:
    rooms: RoomService
    caller: Optional[ModelCaller] = None
    # 统计：用于验收「未提问无 W1 调用」
    call_log: list[dict[str, Any]] = field(default_factory=list)

    # ---- 空闲 / 唤醒 ----
    def has_user_question(self, room_id: str) -> bool:
        room = self.rooms.get_room(room_id)
        return bool(room and room.pinned_question)

    def assert_awake(self, room_id: str, step: str) -> None:
        if not self.has_user_question(room_id):
            raise StepError(f"未提问：房间空闲，不得调用 {step}")

    def primary_worker_id(self, room_id: str) -> Optional[str]:
        """P0 单工人：优先首答 Agent，否则邀请列表第一人。"""
        room = self.rooms.get_room(room_id)
        if not room:
            return None
        if room.first_answerer_agent_id:
            return room.first_answerer_agent_id
        return room.invited_agent_ids[0] if room.invited_agent_ids else None

    # ---- 上下文隔离 ----
    def worker_context(self, viewer_id: str, room_id: str) -> dict[str, Any]:
        return self.rooms.agents.context_for(viewer_id, room_id)

    def judge_context(self, viewer_id: str, room_id: str) -> dict[str, Any]:
        """评议上下文：显式排除他模私有思考（含工人）。"""
        ctx = self.rooms.agents.context_for(viewer_id, room_id)
        # 双保险：peer_thoughts 已为空；再清 own 以外
        self.rooms.agents.thoughts.assert_no_leak(
            viewer_id,
            self.rooms.agents.roles.get_or_create(room_id).invited_agent_ids,
        )
        return ctx

    # ---- 步骤 ----
    def run_w1(
        self,
        room_id: str,
        agent_id: Optional[str] = None,
        *,
        response: Optional[str] = None,
        history: Optional[list[dict[str, Any]]] = None,
    ) -> StepResult:
        step = "W1"
        try:
            self.assert_awake(room_id, step)
        except StepError as exc:
            log_event("agent_idle_block", str(exc), room_id=room_id)
            return StepResult(False, step, agent_id or "", error=str(exc))

        agent_id = agent_id or self.primary_worker_id(room_id)
        if not agent_id:
            return StepResult(False, step, "", error="无可用工人")

        room = self.rooms.get_room(room_id)
        assert room and room.pinned_question
        prompt = build_w1_prompt(question=room.pinned_question, history=history)
        raw, called = self._invoke(agent_id, prompt, response)
        # 首答正文入库（M4 前用壳层 stub）
        self.rooms.agents.sessions.open(agent_id, room_id).append_turn("assistant", raw)
        self.rooms.submit_first_answer(room_id, agent_id, raw)
        log_event("agent_w1", f"agent={agent_id}", room_id=room_id)
        return StepResult(
            True, step, agent_id, prompt=prompt, raw_output=raw, model_called=called
        )

    def run_w2(
        self,
        room_id: str,
        agent_id: Optional[str] = None,
        *,
        response: Optional[str] = None,
    ) -> StepResult:
        step = "W2"
        try:
            self.assert_awake(room_id, step)
        except StepError as exc:
            return StepResult(False, step, agent_id or "", error=str(exc))

        agent_id = agent_id or self.primary_worker_id(room_id)
        if not agent_id:
            return StepResult(False, step, "", error="无可用工人")
        room = self.rooms.get_room(room_id)
        assert room and room.pinned_question
        doc = "\n".join(self.rooms.get_doc(room_id).render_lines())
        prompt = build_w2_prompt(
            question=room.pinned_question, first_answer=doc
        )
        raw, called = self._invoke(agent_id, prompt, response)
        proto = self.rooms.ingest_worker_output(room_id, agent_id, raw)
        log_event("agent_w2", f"ok={proto.ok} kind={getattr(proto.message,'kind',None)}", room_id=room_id)
        return StepResult(
            proto.ok,
            step,
            agent_id,
            prompt=prompt,
            raw_output=raw,
            proto=proto,
            error="" if proto.ok else proto.error,
            model_called=called,
        )

    def run_w3(
        self,
        room_id: str,
        agent_id: Optional[str] = None,
        *,
        changed_set: Optional[list[str]] = None,
        response: Optional[str] = None,
    ) -> StepResult:
        return self._run_review_step(
            "W3", room_id, agent_id, changed_set=changed_set, response=response, confirm=False
        )

    def run_w4(
        self,
        room_id: str,
        agent_id: Optional[str] = None,
        *,
        changed_set: Optional[list[str]] = None,
        response: Optional[str] = None,
    ) -> StepResult:
        if not changed_set:
            return StepResult(
                False, "W4", agent_id or "", error="确认轮必须提供 ChangedSet"
            )
        return self._run_review_step(
            "W4", room_id, agent_id, changed_set=changed_set, response=response, confirm=True
        )

    def _run_review_step(
        self,
        step: str,
        room_id: str,
        agent_id: Optional[str],
        *,
        changed_set: Optional[list[str]],
        response: Optional[str],
        confirm: bool,
    ) -> StepResult:
        try:
            self.assert_awake(room_id, step)
        except StepError as exc:
            return StepResult(False, step, agent_id or "", error=str(exc))
        agent_id = agent_id or self.primary_worker_id(room_id)
        if not agent_id:
            return StepResult(False, step, "", error="无可用工人")
        room = self.rooms.get_room(room_id)
        assert room and room.pinned_question
        doc = "\n".join(self.rooms.get_doc(room_id).render_lines())
        if confirm:
            prompt = build_w4_prompt(
                question=room.pinned_question,
                current_doc=doc,
                changed_set=list(changed_set or []),
            )
        else:
            prompt = build_w3_prompt(
                question=room.pinned_question,
                current_doc=doc,
                changed_set=changed_set,
            )
        raw, called = self._invoke(agent_id, prompt, response)
        proto = self.rooms.ingest_worker_output(room_id, agent_id, raw)
        return StepResult(
            proto.ok,
            step,
            agent_id,
            prompt=prompt,
            raw_output=raw,
            proto=proto,
            error="" if proto.ok else proto.error,
            model_called=called,
        )

    def run_j1(
        self,
        room_id: str,
        agent_id: str,
        *,
        response: Optional[str] = None,
        open_rejects: Optional[list[str]] = None,
    ) -> StepResult:
        step = "J1"
        try:
            self.assert_awake(room_id, step)
        except StepError as exc:
            return StepResult(False, step, agent_id, error=str(exc))

        room = self.rooms.get_room(room_id)
        assert room and room.pinned_question
        # 评议不得读工人私有思考
        self.judge_context(agent_id, room_id)
        pending = self.rooms.review.pending_as_dicts(room_id)
        doc = "\n".join(self.rooms.get_doc(room_id).render_lines())
        prompt = build_j1_prompt(
            question=room.pinned_question,
            current_doc=doc,
            pending_patches=pending,
            open_rejects=open_rejects,
        )
        # 泄漏防护：工人私有内容不得出现在 J1 prompt
        for peer in room.invited_agent_ids:
            if peer == agent_id:
                continue
            for item in self.rooms.agents.thoughts.read(peer):
                if prompt_contains_private_leak(prompt, item.get("text") or ""):
                    return StepResult(
                        False,
                        step,
                        agent_id,
                        prompt=prompt,
                        error="J1 上下文泄漏工人私有思考",
                    )

        raw, called = self._invoke(agent_id, prompt, response)
        # 工人不得直接变 JudgeApprove：若 agent 是工人且输出 Approve，PROTO/职责会拦
        proto = self.rooms.ingest_judge_output(room_id, agent_id, raw)
        return StepResult(
            proto.ok,
            step,
            agent_id,
            prompt=prompt,
            raw_output=raw,
            proto=proto,
            error="" if proto.ok else proto.error,
            model_called=called,
        )

    def _invoke(
        self,
        agent_id: str,
        prompt: MessageList,
        response: Optional[str],
    ) -> tuple[str, bool]:
        prompt = sanitize_messages(prompt)
        if response is not None:
            self.call_log.append(
                {"agent_id": agent_id, "injected": True, "n_msgs": len(prompt)}
            )
            return response, False
        if self.caller is None:
            raise StepError("未配置 ModelCaller，且未提供 response 注入")
        self.call_log.append(
            {"agent_id": agent_id, "injected": False, "n_msgs": len(prompt)}
        )
        text = self.caller(prompt, agent_id)
        return text, True
