"""M2 门面：共享聊天室壳层（T-M2-01～12）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from multi_agent_room.agent_service import AgentService
from multi_agent_room.config import load_config, save_config
from multi_agent_room.event_bus import BusEvent, EventBus
from multi_agent_room.logging_setup import log_event
from multi_agent_room.model_service import ModelService
from multi_agent_room.orchestrator import Orchestrator
from multi_agent_room.paths import ensure_workspace_dir, normalize_workspace_path
from multi_agent_room.phase_machine import DEFAULT_AGENDA
from multi_agent_room.audit_store import AuditStore
from multi_agent_room.confirm_service import ALLOW_SKIP_CONFIRM, ConfirmService
from multi_agent_room.conflict import classify_target_conflict, stack_replaces
from multi_agent_room.deliver_service import DeliverResult, DeliverService
from multi_agent_room.exec_service import ExecRunResult, ExecService
from multi_agent_room.judge_service import (
    JudgeService,
    OpenReject,
    make_merge_record,
)
from multi_agent_room.memory_service import MemoryService, SharedKind
from multi_agent_room.patch_filter import PatchItem
from multi_agent_room.protocol import NormalizeResult, ProtocolNormalizer
from multi_agent_room.review_service import ReviewService
from multi_agent_room.room import Room, RoomStore, new_room_id
from multi_agent_room.room_events import EventTimeline
from multi_agent_room.room_state import RoomState, RoomStateStore
from multi_agent_room.shared_doc import DocBlock, DocService, SharedDoc, SharedDocView
from multi_agent_room.tool_host import RoomToolContext, ToolHost, ToolResult


# 评判操作台命令（T-M6）
JUDGE_COMMANDS = (
    "Accept",
    "AcceptPatch",
    "Merge",
    "MergeConflict",
    "R1",
    "R2",
    "R3",
    "JudgeApprove",
    "MarkTrivial",
    "AuthorizeDeliver",  # P1a 控件预留
)


class RoomService:
    def __init__(
        self,
        *,
        agents: Optional[AgentService] = None,
        models: Optional[ModelService] = None,
        store: Optional[RoomStore] = None,
        orchestrator: Optional[Orchestrator] = None,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.models = models or ModelService()
        self.agents = agents or AgentService(models=self.models)
        self.store = store or RoomStore()
        self.orch = orchestrator or Orchestrator()
        self.bus = bus or EventBus()
        self.timeline = EventTimeline()
        self.proto = ProtocolNormalizer(thoughts=self.agents.thoughts)
        self.docs = DocService()
        self.review = ReviewService()
        self.judge = JudgeService()
        self.confirm = ConfirmService()
        self.audit = AuditStore()
        self.state_store = RoomStateStore()
        self.memory = MemoryService()
        self.tools = ToolHost(bus=self.bus)
        self.deliver_svc = DeliverService()
        self.exec_svc = ExecService()
        self._docs: dict[str, SharedDocView] = {}
        self._last_delivery: dict[str, DeliverResult] = {}
        self._current_room_id: Optional[str] = None
        # 审阅超时累计暂停（Frozen / Clarify）
        self._pause_started: dict[str, float] = {}
        # UI 时间线镜像总线事件（按房间订阅在首次入房时建立）
        self._bus_mirrors: dict[str, str] = {}

    # ---- 房间 CRUD ----
    def create_room(self, title: str, *, workspace_path: Optional[str] = None) -> Room:
        room = Room(
            room_id=new_room_id(),
            title=(title.strip() or "未命名房间"),
            workspace_path=str(normalize_workspace_path(workspace_path))
            if workspace_path
            else None,
            agenda=list(DEFAULT_AGENDA),
        )
        self.store.upsert(room)
        st = self.orch.bind(room)
        room.agenda = list(st.agenda)
        self.store.upsert(room)
        self._docs[room.room_id] = SharedDocView(
            room_id=room.room_id, _svc=self.docs
        )
        self.timeline.append(room.room_id, "RoomCreated", f"title={room.title}")
        self._ensure_bus_mirror(room.room_id)
        self.bus.signal_idle(room.room_id, reason="room_created")
        log_event("room_create", f"id={room.room_id} title={room.title}")
        self._current_room_id = room.room_id
        return room

    def list_rooms(self) -> list[Room]:
        return self.store.list_all()

    def enter_room(self, room_id: str) -> Room:
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        self._ensure_bus_mirror(room_id)
        self._current_room_id = room_id
        self.timeline.append(room_id, "RoomEnter", "进入房间", collapsed=True)
        return room

    def current_room(self) -> Optional[Room]:
        if not self._current_room_id:
            return None
        return self.store.get(self._current_room_id)

    def get_room(self, room_id: str) -> Optional[Room]:
        return self.store.get(room_id)

    def get_doc(self, room_id: str) -> SharedDocView:
        if room_id not in self._docs:
            self._docs[room_id] = SharedDocView(room_id=room_id, _svc=self.docs)
        view = self._docs[room_id]
        view._svc = self.docs
        view._sync_from_active()
        return view

    # ---- 邀请就绪 Agent ----
    def list_ready_agents(self) -> list[Any]:
        ready_ids = {m.config_id for m in self.models.list_bindable()}
        return [
            a
            for a in self.agents.list_agents()
            if a.model_config_id in ready_ids
        ]

    def invite_ready_agent(self, room_id: str, agent_id: str) -> Room:
        room = self._require(room_id)
        profile = self.agents.profiles.get(agent_id)
        if not profile:
            raise KeyError(f"Agent 不存在: {agent_id}")
        bindable = {m.config_id for m in self.models.list_bindable()}
        if profile.model_config_id not in bindable:
            raise ValueError("未就绪模型不可邀请入房（DEP-01）")
        self.agents.invite_to_room(room_id, agent_id)
        if agent_id not in room.invited_agent_ids:
            room.invited_agent_ids.append(agent_id)
        self.store.upsert(room)
        self.timeline.append(
            room_id, "Invite", f"agent={agent_id} model={profile.model_config_id}"
        )
        return room

    # ---- 提问 / 原话钉选 ----
    def ask_question(self, room_id: str, question: str) -> Room:
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        if not room.invited_agent_ids:
            raise ValueError("请先邀请至少一名就绪 Agent")
        q = question.strip()
        if not q:
            raise ValueError("问题不能为空")
        self.agents.user_ask(room_id, q)
        room.pinned_question = q
        room.final_reply = None
        room.gate_passed = False
        room.first_answerer_agent_id = None
        room.first_answerer_config_id = None
        room.review_window_open = False
        room.escalation_hint = ""
        self.confirm.reset(room_id)
        if room.phase == "Final":
            ok, msg = self.orch.transition(room_id, "Idle", reason="新提问")
            if not ok:
                raise ValueError(msg)
        if room.phase not in ("Idle", "Campaign"):
            ok, msg = self.orch.transition(
                room_id, "Idle", reason="重置提问", force_special=True
            )
            if not ok:
                raise ValueError(msg)
        if room.phase == "Idle":
            ok, msg = self.orch.transition(room_id, "Campaign", reason="用户提问")
            if not ok:
                raise ValueError(msg)
        self.orch.require(room_id).budget.reset_round()
        self.store.upsert(room)
        self.timeline.append(room_id, "UserAsk", "原话已钉选")
        self.bus.signal_awake(room_id, reason="user_ask")
        # M9：原话约束进共享记忆（过程态，不得 resolved）
        try:
            self.memory.write_shared(
                room_id,
                "anchor_summary",
                q[:500],
                tags=["user_ask"],
                resolved=False,
                gate_passed=False,
            )
            self.memory.write_shared(
                room_id,
                "constraint",
                f"原话钉选不可静默改写：{q[:200]}",
                tags=["pin"],
                resolved=False,
                gate_passed=False,
            )
        except Exception as exc:  # noqa: BLE001
            log_event("memory_write_skip", str(exc), room_id=room_id)
        return room

    def append_chat_turn(
        self, room_id: str, role: str, text: str, *, persist: bool = True
    ) -> Room:
        room = self._require(room_id)
        body = (text or "").strip()
        if not body:
            return room
        if not hasattr(room, "chat_turns") or room.chat_turns is None:
            room.chat_turns = []
        room.chat_turns.append(
            {"role": role, "text": body, "ts": time.time()}
        )
        if persist:
            self.store.upsert(room)
        return room

    def save_chat_attachments(
        self,
        room_id: str,
        attachments: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """把用户附件落到工作区 `_mar_inbox/`（真实文件，禁止软链接）。"""
        room = self._require(room_id)
        if not room.workspace_path:
            raise ValueError("请先绑定工作区再上传附件")
        from multi_agent_room.file_tools import file_write

        saved: list[dict[str, str]] = []
        for i, att in enumerate(attachments or []):
            name = str(att.get("name") or f"file_{i}").replace("\\", "/").split("/")[-1]
            name = "".join(c for c in name if c.isalnum() or c in "._- ")[:120].strip() or f"file_{i}"
            content = str(att.get("content") or "")
            if att.get("encoding") == "base64":
                import base64

                try:
                    raw = base64.b64decode(content)
                    # 尝试 utf-8；失败则写二进制标记说明
                    try:
                        content = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        # 仍写入 latin-1 可逆表示不理想；改为写 bytes via path
                        from multi_agent_room.file_tools import resolve_in_workspace

                        rel = f"_mar_inbox/{name}"
                        p = resolve_in_workspace(room.workspace_path, rel)
                        if p.exists() and p.is_symlink():
                            raise PermissionError(f"禁止写入软链接: {rel}")
                        p.parent.mkdir(parents=True, exist_ok=True)
                        tmp = p.with_name(p.name + ".mar_tmp")
                        if tmp.is_symlink():
                            raise PermissionError("临时路径为软链接")
                        tmp.write_bytes(raw)
                        tmp.replace(p)
                        saved.append({"name": name, "path": rel, "kind": "binary"})
                        continue
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"附件解码失败 {name}: {exc}") from exc
            rel = f"_mar_inbox/{name}"
            file_write(room.workspace_path, rel, content)
            saved.append({"name": name, "path": rel, "kind": "text"})
        if saved:
            self.timeline.append(
                room_id,
                "Attach",
                "附件: " + ", ".join(s["path"] for s in saved),
            )
        return saved

    def ask_and_generate(
        self,
        room_id: str,
        question: str,
        *,
        auto_w1: bool = True,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """提问并在单模型场景自动回复；有工作区时走 Cursor 式工具循环。"""
        room = self._require(room_id)
        prior_turns = list(getattr(room, "chat_turns", None) or [])
        saved_atts: list[dict[str, str]] = []
        q = (question or "").strip()
        if attachments:
            saved_atts = self.save_chat_attachments(room_id, attachments)
            paths = ", ".join(a["path"] for a in saved_atts)
            q = (q + f"\n\n【用户附件已落到工作区】\n{paths}").strip()
        if not q:
            raise ValueError("问题不能为空")

        room = self.ask_question(room_id, q)
        self.append_chat_turn(room_id, "user", q)
        out: dict[str, Any] = {
            "room": self._require(room_id),
            "mode": "campaign",
            "w1_ok": False,
            "w1_error": "",
            "w1_reply": "",
            "degrade_a": False,
            "tool_steps": [],
            "attachments": saved_atts,
            "ui_text": "已提问，进入竞选",
        }
        if not auto_w1:
            return out

        bindable_n = len(self.models.list_bindable())
        invited_n = len(room.invited_agent_ids)
        single = bindable_n <= 1 or invited_n == 1
        if not single:
            out["ui_text"] = "已提问；多模型竞选中（可稍后指定职责或等待冻结）"
            out["room"] = self._require(room_id)
            return out

        agent_id = room.invited_agent_ids[0]
        self.agents.roles.set_ready_model_count(room_id, max(1, bindable_n))
        ok, reason = self.agents.user_assign_roles(
            room_id,
            first_answerer_agent_id=agent_id,
            judge_is_user=True,
        )
        if not ok:
            out["w1_error"] = reason
            out["ui_text"] = f"单模型指派失败：{reason}"
            out["room"] = self._require(room_id)
            return out

        try:
            room = self.freeze_roles_and_await_first(room_id)
        except ValueError as exc:
            out["w1_error"] = str(exc)
            out["ui_text"] = f"冻结职责失败：{exc}"
            out["room"] = self._require(room_id)
            return out

        out["degrade_a"] = True
        out["mode"] = "single_model"
        profile = self.agents.profiles.get(agent_id)
        if not profile:
            out["w1_error"] = "Agent 资料缺失"
            out["ui_text"] = out["w1_error"]
            out["room"] = self._require(room_id)
            return out

        # 有工作区 → 必须走工具循环（禁止纯口嗨落盘）
        room = self._require(room_id)
        if room.workspace_path:
            log_event(
                "ask_route",
                f"agent_tools ws={room.workspace_path}",
                room_id=room_id,
            )
            try:
                result = self._run_tool_loop_reply(
                    room_id,
                    agent_id=agent_id,
                    config_id=profile.model_config_id,
                    question=q,
                    prior_turns=prior_turns,
                    has_attachments=bool(saved_atts),
                )
            except Exception as exc:  # noqa: BLE001
                out["w1_error"] = str(exc)
                out["ui_text"] = f"工具循环异常：{exc}"
                out["room"] = self._require(room_id)
                log_event("tool_loop_exc", str(exc), room_id=room_id)
                return out
            out["tool_steps"] = result.get("tool_steps") or []
            out["w1_ok"] = bool(result.get("ok"))
            out["w1_reply"] = result.get("reply") or ""
            out["w1_error"] = result.get("error") or ""
            out["mode"] = "agent_tools"
            if out["w1_ok"]:
                try:
                    self.submit_first_answer(room_id, agent_id, out["w1_reply"])
                except Exception as exc:  # noqa: BLE001
                    # 工具已执行完，入库失败仍把回复给用户
                    log_event("submit_after_tools_fail", str(exc), room_id=room_id)
                self.append_chat_turn(room_id, "assistant", out["w1_reply"])
                out["room"] = self._require(room_id)
                n = len(out["tool_steps"])
                writes = sum(
                    1
                    for s in out["tool_steps"]
                    if s.get("ok") and s.get("name") in ("file_write", "search_replace")
                )
                out["ui_text"] = (
                    f"工具循环完成：调用 {n} 次（其中写入 {writes} 次）。"
                    "请在工作区目录核实文件。"
                )
                self.timeline.append(
                    room_id, "W1", f"agent_tools agent={agent_id} tools={n} writes={writes}"
                )
            else:
                out["ui_text"] = f"工具循环失败：{out['w1_error']}"
                out["room"] = self._require(room_id)
            return out

        log_event("ask_route", "plain_w1 no_workspace", room_id=room_id)
        from multi_agent_room.worker_runtime import AgentRuntime, StepError

        models = self.models
        agents = self.agents

        def _caller(messages: list[dict[str, str]], aid: str) -> str:
            p = agents.profiles.get(aid)
            if not p:
                raise StepError(f"Agent 不存在: {aid}")
            _cfg, cres = models.chat(p.model_config_id, messages, max_tokens=2048)
            if not cres.ok:
                raise StepError(cres.ui_text or cres.message or "模型调用失败")
            if not (cres.reply or "").strip():
                raise StepError("模型返回空内容")
            return cres.reply.strip()

        runtime = AgentRuntime(rooms=self, caller=_caller)
        try:
            step = runtime.run_w1(room_id, agent_id, history=prior_turns)
        except StepError as exc:
            out["w1_error"] = str(exc)
            out["ui_text"] = f"单模型首答失败：{exc}"
            out["room"] = self._require(room_id)
            return out
        except Exception as exc:  # noqa: BLE001
            out["w1_error"] = str(exc)
            out["ui_text"] = f"单模型首答异常：{exc}"
            out["room"] = self._require(room_id)
            log_event("ask_w1_exc", str(exc), room_id=room_id)
            return out

        out["room"] = self._require(room_id)
        out["w1_ok"] = bool(step.ok)
        out["w1_reply"] = step.raw_output or ""
        out["w1_error"] = step.error or ""
        if step.ok:
            self.append_chat_turn(room_id, "assistant", step.raw_output or "")
            out["room"] = self._require(room_id)
            out["ui_text"] = "已回复（未绑定工作区，无文件工具）。可继续追问。"
            self.timeline.append(room_id, "W1", f"单模型回复 agent={agent_id}")
        else:
            out["ui_text"] = f"单模型首答失败：{step.error}"
        return out

    def _run_tool_loop_reply(
        self,
        room_id: str,
        *,
        agent_id: str,
        config_id: str,
        question: str,
        prior_turns: list[dict[str, Any]],
        has_attachments: bool,
    ) -> dict[str, Any]:
        from multi_agent_room.tool_loop import AgentToolLoop, build_agent_system_prompt

        room = self._require(room_id)
        assert room.workspace_path
        # 聊天写盘：发放短期 writeToken（真实文件落盘，禁止软链接）
        self.authorize_deliver(room_id, scope="chat_agent_write", ttl_sec=3600)
        for sid in (
            "file.read",
            "dir.list",
            "file.write",
            "search.replace",
            "file.delete",
            "glob.search",
        ):
            try:
                self.tools.skills.authorize(sid, room_id=room_id, agent_id=agent_id)
            except Exception:  # noqa: BLE001
                pass

        log_event(
            "agent_tools_begin",
            f"agent={agent_id} ws={room.workspace_path}",
            room_id=room_id,
        )

        msgs: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": build_agent_system_prompt(
                    workspace=room.workspace_path,
                    has_attachments=has_attachments,
                ),
            }
        ]
        for t in prior_turns:
            role = "assistant" if t.get("role") in ("assistant", "agent") else "user"
            text = str(t.get("text") or "").strip()
            if text:
                msgs.append({"role": role, "content": text})
        msgs.append({"role": "user", "content": question})

        ctx = self._tool_ctx(room_id, agent_id=agent_id)
        loop = AgentToolLoop(
            chat_fn=self.models.chat,
            tools=self.tools,
            ctx=ctx,
            timeline_append=self.timeline.append,
            max_rounds=8,
        )
        result = loop.run(msgs, config_id=config_id)
        return {
            "ok": result.ok,
            "reply": result.reply,
            "error": result.error,
            "tool_steps": [
                {
                    "name": s.name,
                    "arguments": {
                        k: (v[:200] + "…") if isinstance(v, str) and len(v) > 200 else v
                        for k, v in (s.arguments or {}).items()
                    },
                    "ok": s.ok,
                    "message": s.message,
                }
                for s in result.steps
            ],
        }

    # ---- 流程推进（phase 经编排器）----
    def freeze_roles_and_await_first(self, room_id: str) -> Room:
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        ok, reason = self.agents.freeze_roles(room_id)
        if not ok:
            raise ValueError(reason)
        st = self.agents.roles.get_or_create(room_id)
        room.judge_is_user = st.roles.judge_is_user
        room.judge_agent_id = st.roles.judge_agent_id
        room.first_answerer_agent_id = st.roles.first_answerer_agent_id
        room.first_answerer_config_id = st.roles.first_answerer_config_id
        tok, tmsg = self.orch.transition(
            room_id, "AwaitingFirstAnswer", reason="职责冻结"
        )
        if not tok:
            raise ValueError(tmsg)
        self.store.upsert(room)
        return room

    def submit_first_answer(
        self, room_id: str, agent_id: str, text: str
    ) -> Room:
        """壳层：记录首答 → 资格锁展示 → 共享稿 stub → ReviewOpen。"""
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        # T-M11-04：入库前骨架配额（编排器强制）
        from multi_agent_room.skeleton_gate import check_skeleton

        sk = check_skeleton(
            text, quota=self.exec_svc.skeleton_quota, label="first_answer"
        )
        if sk.rejected:
            raise ValueError(sk.message)
        profile = self.agents.profiles.get(agent_id)
        if not profile:
            raise KeyError(f"Agent 不存在: {agent_id}")
        if agent_id not in room.invited_agent_ids:
            raise ValueError("Agent 未入房")
        st = self.agents.roles.get_or_create(room_id)
        if not st.roles.frozen:
            self.agents.user_assign_roles(
                room_id, first_answerer_agent_id=agent_id
            )
            st = self.agents.roles.get_or_create(room_id)
        room.first_answerer_agent_id = st.roles.first_answerer_agent_id or agent_id
        room.first_answerer_config_id = (
            st.roles.first_answerer_config_id or profile.model_config_id
        )
        room.judge_is_user = st.roles.judge_is_user
        room.judge_agent_id = st.roles.judge_agent_id

        doc = self.get_doc(room_id)
        # M4：正式切块入库 → V1 active
        shared = self.docs.create_from_first_answer(
            room_id, text, agent_id=agent_id, activate=True
        )
        for b in shared.active_blocks():
            self.judge.note_block_author(room_id, b.block_id, agent_id)
        doc._sync_from_active()
        room.review_window_open = True
        timeout_ms = int(room.review_seconds * 1000)
        win = self.review.open_window(
            room_id,
            shared.version,
            timeout_ms=timeout_ms,
            quiet_period_ms=self.review.quiet_period_ms,
        )
        room.review_deadline_ts = win._timeout_deadline
        # R2 后新首答：短暂放开全文重写误杀
        if shared.base_from == "firstAnswer" and room.phase in (
            "AwaitingFirstAnswer",
            "Campaign",
            "ReviewOpen",
        ):
            # 若上一轮曾 R2 void，允许本轮首答链路持 bypass（贴补丁时一次性）
            hist = self.docs.store._history.get(room_id) or []
            if any(getattr(d, "voided_reason", "") == "R2" for d in hist):
                self.review.allow_r2_rewrite_bypass(room_id, True)

        # Campaign → AwaitingFirstAnswer → ReviewOpen
        if room.phase == "Campaign":
            tok, tmsg = self.orch.transition(
                room_id, "AwaitingFirstAnswer", reason="进入首答"
            )
            if not tok:
                raise ValueError(tmsg)
        if room.phase == "AwaitingFirstAnswer":
            tok, tmsg = self.orch.transition(
                room_id, "ReviewOpen", reason="首答入库"
            )
            if not tok:
                raise ValueError(tmsg)
        elif room.phase != "ReviewOpen":
            tok, tmsg = self.orch.transition(
                room_id, "ReviewOpen", reason="首答入库", force_special=True
            )
            if not tok:
                raise ValueError(tmsg)

        self.store.upsert(room)
        self.bus.publish(
            room_id,
            "DocVersion",
            {
                "version": shared.version,
                "block_ids": [b.block_id for b in shared.active_blocks()],
                "reason": "first_answer",
                "doc_id": shared.doc_id,
            },
        )
        self.timeline.append(
            room_id,
            "DocVersion",
            f"首答入库 V{shared.version} blocks={len(shared.active_blocks())}",
            collapsed=False,
        )
        self.timeline.append(
            room_id,
            "QualificationLock",
            room.qualification_lock_text(),
        )
        self._after_mutation(
            room_id,
            audit_type="DocVersion",
            payload={"version": shared.version, "reason": "first_answer"},
        )
        return room

    # ---- 打断 / 恢复：只发命令，编排器写 frozen ----
    def interrupt(self, room_id: str) -> Room:
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        self._begin_pause(room_id)
        ok, msg = self.orch.interrupt(room_id)
        if not ok:
            self._pause_started.pop(room_id, None)
            raise ValueError(msg)
        self.review.sync_pause(
            room_id, frozen=True, clarify_hold=room.clarify_hold
        )
        self.store.upsert(room)
        self.timeline.append(room_id, "Interrupt", "Frozen=true；审阅窗保持")
        return room

    def resume(self, room_id: str) -> Room:
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        self._end_pause(room)
        ok, msg = self.orch.resume(room_id)
        if not ok:
            raise ValueError(msg)
        self.review.sync_pause(
            room_id, frozen=False, clarify_hold=room.clarify_hold
        )
        self.store.upsert(room)
        self.timeline.append(room_id, "Resume", f"恢复 phase={room.phase}")
        return room

    # ---- 澄清 ----
    def ask_clarify(self, room_id: str, question: str, *, from_agent: str = "") -> Room:
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        q = question.strip()
        if not q:
            raise ValueError("澄清问题不能为空")
        if not room.clarify_hold:
            self._begin_pause(room_id)
        ok, msg = self.orch.set_clarify_hold(room_id, q)
        if not ok:
            raise ValueError(msg)
        self.store.upsert(room)
        self.timeline.append(
            room_id,
            "Clarify",
            f"from={from_agent or '?'} q={q[:80]}",
        )
        self.bus.publish(
            room_id,
            "Clarify",
            {"question": q, "from_agent": from_agent},
        )
        self.review.sync_pause(
            room_id, frozen=room.frozen, clarify_hold=True
        )
        return room

    def answer_clarify(self, room_id: str, answer: str) -> Room:
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        if not room.clarify_hold and room.phase != "AwaitingUserClarify":
            raise ValueError("当前无待答澄清")
        self._end_pause(room)
        ok, msg = self.orch.clear_clarify_hold(room_id)
        if not ok:
            raise ValueError(msg)
        self.review.sync_pause(
            room_id, frozen=room.frozen, clarify_hold=False
        )
        self.store.upsert(room)
        self.timeline.append(room_id, "ClarifyAnswer", answer.strip()[:120])
        return room

    def try_silence_pass(self, room_id: str, *, now: Optional[float] = None) -> tuple[bool, str]:
        """尝试按 M5 规则关窗；超时未表态 ≠ 同意；不写最终回复。"""
        room = self._require(room_id)
        now = time.time() if now is None else now
        if room.frozen or room.phase == "Frozen":
            return False, "Frozen：不计超时沉默通过；审阅窗不关闭"
        if room.clarify_hold or room.phase == "AwaitingUserClarify":
            return False, "待澄清：不计超时沉默通过；审阅窗不关闭"
        if not room.review_window_open:
            return False, "审阅窗未开"
        eff = self.effective_reviewers(room_id)
        result = self.review.try_close(room_id, eff, force=False, now=now)
        if not result.closed:
            return False, result.reason
        room.review_window_open = False
        if room.phase == "ReviewOpen":
            tok, tmsg = self.orch.transition(
                room_id, "AwaitingJudge", reason=result.reason
            )
            if not tok:
                raise ValueError(tmsg)
        self.store.upsert(room)
        self.timeline.append(
            room_id,
            "ReviewClosed",
            f"reason={result.reason} queue={len(result.queue)} auto_final=0",
        )
        self.bus.publish(
            room_id,
            "QueueUpdated",
            {"queue": [p.patch_id for p in result.queue], "handed_to_m6": True},
        )
        return True, result.reason

    def force_submit_judge(self, room_id: str) -> tuple[bool, str]:
        """强制关窗交 M6。"""
        room = self._require(room_id)
        eff = self.effective_reviewers(room_id)
        result = self.review.try_close(room_id, eff, force=True)
        if not result.closed:
            return False, result.reason
        room.review_window_open = False
        if room.phase == "ReviewOpen":
            self.orch.transition(room_id, "AwaitingJudge", reason="force_judge")
        self.store.upsert(room)
        return True, result.reason

    def effective_reviewers(self, room_id: str) -> list[str]:
        """有效审阅者：显式列表去弃权；否则除 judge 外全部；空则用户回退。"""
        base = self.agents.roles.effective_reviewers(room_id)
        win = self.review.get_window(room_id)
        if win:
            return win.effective_reviewers(base, include_user_fallback=True)
        if not base:
            return ["__user__"]
        return base

    def silent_agree_map(self, room_id: str) -> dict[str, bool]:
        return self.review.silent_agree_map(
            room_id, self.effective_reviewers(room_id)
        )

    def review_window_still_open(self, room_id: str) -> bool:
        return bool(self._require(room_id).review_window_open)

    # ---- 最终回复槽（T-M2-06）----
    def final_slot_text(self, room_id: str) -> str:
        return self._require(room_id).final_slot_text()

    def commit_final(self, room_id: str, text: str) -> Room:
        """仅 Final + 门禁后写入正文。"""
        room = self._require(room_id)
        if room.phase != "Final" or not room.gate_passed:
            raise ValueError("门禁前不可写入最终回复槽")
        room.final_reply = text.strip()
        self.store.upsert(room)
        self.timeline.append(room_id, "FinalCommitted", "终稿写入槽")
        self.bus.publish(
            room_id,
            "FinalCommitted",
            {"length": len(room.final_reply)},
        )
        return room

    def commit_final_reply(
        self, room_id: str, text: Optional[str] = None
    ) -> Room:
        """T-M7-05：CommitFinalReply — 须已 JudgeApprove。"""
        room = self._require(room_id)
        if not room.gate_passed or room.phase != "Final":
            raise ValueError("CommitFinalReply 前置：必须已有本轮成功的 JudgeApprove")
        doc = self.docs.store.get_active(room_id)
        if text is None:
            text = doc.full_text() if doc else ""
        room = self.commit_final(room_id, text)
        # M9：终稿指针进共享记忆（可检索）
        try:
            self.memory.write_shared(
                room_id,
                "final_pointer",
                (text or "")[:2000],
                tags=["final"],
                resolved=True,
                gate_passed=True,
                meta={
                    "doc_id": doc.doc_id if doc else "",
                    "version": doc.version if doc else 0,
                    "phase": room.phase,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log_event("memory_final_skip", str(exc), room_id=room_id)
        return room

    def mark_confirm_clean(self, room_id: str) -> tuple[bool, str]:
        """确认轮干净结算（已读齐且无有效补丁）；不自动 Final。"""
        room = self._require(room_id)
        st = self.confirm.get(room_id)
        if not st.active and room.phase != "ConfirmOpen":
            return False, "确认轮未开"
        pending = len(self.review.pending(room_id))
        if not self.confirm.is_clean(
            room_id,
            effective_reviewers=self.effective_reviewers(room_id),
            pending_count=pending,
        ):
            return False, "确认轮未干净"
        self.confirm.mark_clean(room_id)
        self.timeline.append(room_id, "ConfirmClean", "等待 JudgeApprove")
        return True, "ok"

    def apply_user_escalation(self, room_id: str, choice: str) -> Room:
        """确认轮封顶后四选项（T-M7-08）。"""
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        if choice == "raise_confirm_cap":
            self.confirm.raise_cap(room_id, 2)
        ok, msg = self.orch.apply_escalation_choice(room_id, choice)
        if not ok:
            raise ValueError(msg)
        if choice == "r2_full_reject":
            self.confirm.reset(room_id)
        self.store.upsert(room)
        return room

    def _make_confirm_ready(self, room_id: str) -> None:
        """测试/编排辅助：全体有效审阅者已读并标记干净。"""
        for aid in self.effective_reviewers(room_id):
            if aid != "__user__":
                self.record_read(room_id, agent_id=aid)
            else:
                self.confirm.mark_read(room_id, "__user__")
        ok, msg = self.mark_confirm_clean(room_id)
        if not ok:
            raise ValueError(msg)

    def mark_gate_passed_demo(self, room_id: str, *, agent_id: Optional[str] = None) -> Room:
        """JudgeApprove：权限 + OpenReject + 确认轮门禁 → Final（写槽另调 CommitFinalReply）。"""
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        if ALLOW_SKIP_CONFIRM:
            raise RuntimeError("ALLOW_SKIP_CONFIRM 必须为 False")
        allowed, reason = self.agents.roles.can_judge_approve(room_id, agent_id)
        if agent_id is None and room.judge_is_user:
            allowed, reason = True, "user"
        if not allowed:
            raise PermissionError(reason)
        if self.judge.has_open_rejects(room_id):
            raise ValueError("OpenReject 未关闭：禁止 Final / JudgeApprove")
        pending = len(self.review.pending(room_id))
        ok_c, msg_c = self.confirm.can_judge_approve(
            room_id,
            phase=room.phase,
            effective_reviewers=self.effective_reviewers(room_id),
            pending_count=pending,
        )
        if not ok_c:
            raise ValueError(msg_c)
        room.gate_passed = True
        room.review_window_open = False
        if room.phase == "ReviewOpen":
            tok, tmsg = self.orch.transition(
                room_id, "AwaitingJudge", reason="JudgeApprove 前"
            )
            if not tok:
                raise ValueError(tmsg)
        tok, tmsg = self.orch.transition(room_id, "Final", reason="JudgeApprove")
        if not tok:
            tok, tmsg = self.orch.transition(
                room_id, "Final", reason="JudgeApprove", force_special=True
            )
            if not tok:
                raise ValueError(tmsg)
        self.confirm.after_judge_approve(room_id)
        self.bus.publish(room_id, "JudgeApprove", {"phase": room.phase})
        self.store.upsert(room)
        return room

    def judge_context(self, room_id: str) -> dict[str, Any]:
        """T-M6-01：原话+稿+队列+开打回+规则；不含闲聊。"""
        room = self._require(room_id)
        doc = self.docs.store.get_active(room_id)
        shared = doc.full_text() if doc else ""
        pending = self.review.pending_as_dicts(room_id)
        rejects = [
            {
                "reject_id": r.reject_id,
                "target": r.target,
                "reason": r.reason,
                "assignee": r.assignee_agent_id,
            }
            for r in self.judge.list_open_rejects(room_id)
        ]
        ctx = self.judge.build_context(
            question=room.pinned_question or "",
            shared_doc=shared,
            pending=self.review.pending(room_id),
        )
        ctx.open_rejects = rejects
        return ctx.as_dict()

    # ---- 评判操作台（T-M6）----
    def judge_command(self, room_id: str, command: str, **payload: Any) -> Room:
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        if command not in JUDGE_COMMANDS:
            raise ValueError(f"未知评判命令: {command}")
        # AcceptPatch 别名
        if command == "AcceptPatch":
            command = "Accept"
        self.timeline.append(
            room_id,
            "JudgeCommand",
            f"{command}",
            collapsed=False,
            **payload,
        )
        log_event("judge_command", f"room={room_id} cmd={command}")
        if command == "JudgeApprove":
            return self.mark_gate_passed_demo(
                room_id, agent_id=payload.get("agent_id")
            )
        if command == "AuthorizeDeliver":
            return self.authorize_deliver(room_id, **payload)
        if command == "MarkTrivial":
            return self._cmd_mark_trivial(room_id, payload)
        if command == "R2":
            return self._cmd_r2(room_id, payload)
        if command == "R1":
            return self._cmd_r1(room_id, payload)
        if command == "R3":
            return self._cmd_r3(room_id, payload)
        if command == "MergeConflict":
            return self._cmd_merge_conflict(room_id, payload)
        if command in ("Accept", "Merge"):
            return self._cmd_accept_or_merge(room_id, command, payload)
        return room

    def _cmd_mark_trivial(self, room_id: str, payload: dict) -> Room:
        room = self._require(room_id)
        patch_id = str(payload.get("patch_id") or "")
        by = payload.get("agent_id")
        allowed, _reason = self.agents.roles.can_judge_approve(
            room_id, by if by is not None else None
        )
        if by is None:
            allowed = True
        ok, msg = self.review.mark_trivial(
            room_id, patch_id, by_agent_id=by, judge_allowed=allowed
        )
        if not ok:
            raise ValueError(msg)
        self.proto._patch_queue[room_id] = [
            m
            for m in self.proto.patch_queue(room_id)
            if str(m.fields.get("patch_id") or "") != patch_id
        ]
        self.bus.publish(
            room_id, "QueueUpdated", {"removed": patch_id, "reason": "MarkTrivial"}
        )
        self.store.upsert(room)
        return room

    def _cmd_r2(self, room_id: str, payload: dict) -> Room:
        room = self._require(room_id)
        ok_v, msg_v = self.judge.validate_r2(payload)
        if not ok_v:
            raise ValueError(msg_v)
        ok, msg = self.orch.record_r2(room_id)
        room.escalation_hint = self.orch.require(room_id).escalation_hint
        self.docs.void_for_r2(room_id, reason="R2")
        room.review_window_open = False
        self.review.allow_r2_rewrite_bypass(room_id, True)
        self.judge.clear_confirm_state(room_id)
        self.review.set_changed_set(room_id, None)
        self.confirm.reset(room_id)
        self.bus.emit_verdict(room_id, "R2", **payload)
        self.store.upsert(room)
        if not ok:
            self.timeline.append(room_id, "BudgetStop", msg)
        return room

    def _cmd_r1(self, room_id: str, payload: dict) -> Room:
        room = self._require(room_id)
        target = str(payload.get("target") or "").strip()
        if not target:
            raise ValueError("R1 缺 target")
        ok_t, reason = self.docs.can_patch_target(room_id, target)
        if not ok_t:
            raise ValueError(reason)
        ok, msg, rej = self.judge.open_r1(
            room_id,
            target=target,
            reason=str(payload.get("reason") or payload.get("claim") or ""),
            assignee_agent_id=str(payload.get("assignee_agent_id") or ""),
            first_answerer=room.first_answerer_agent_id or "",
        )
        if not ok:
            self.orch._escalate(room_id, msg)
            raise ValueError(msg)
        if room.phase == "ReviewOpen":
            self.orch.transition(room_id, "AwaitingJudge", reason="R1")
        self.bus.emit_verdict(room_id, "R1", **payload)
        self.timeline.append(
            room_id,
            "OpenReject",
            f"id={rej.reject_id if rej else '?'} target={target}",
        )
        self.store.upsert(room)
        self._after_mutation(
            room_id,
            audit_type="R1",
            payload={
                "target": target,
                "reason": str(payload.get("reason") or ""),
                "reject_id": rej.reject_id if rej else "",
            },
        )
        return room

    def _cmd_r3(self, room_id: str, payload: dict) -> Room:
        room = self._require(room_id)
        targets = payload.get("targets") or (
            [payload["target"]] if payload.get("target") else []
        )
        targets = [str(t) for t in targets]
        replaces = payload.get("replaces") or {}
        if payload.get("target") and payload.get("replace") is not None:
            replaces = {str(payload["target"]): str(payload["replace"])}
        doc = self.docs.require_active(room_id)
        old_by = {t: (doc.get_block(t).text if doc.get_block(t) else "") for t in targets}
        new_by = {t: str(replaces.get(t, "")) for t in targets}
        ok_v, msg_v = self.judge.validate_r3(
            targets=targets,
            old_by_target=old_by,
            new_by_target=new_by,
            claim=str(payload.get("claim") or payload.get("reason") or ""),
        )
        if not ok_v:
            raise ValueError(msg_v)
        if room.phase == "ReviewOpen":
            self.orch.transition(room_id, "AwaitingJudge", reason="R3")
        for t in targets:
            self.docs.apply_replace(room_id, t, new_by[t], bump=False)
        shared = self.docs.bump_version(room_id, base_from="tweak")
        self.judge.set_changed_set(room_id, targets)
        self.review.set_changed_set(room_id, set(targets))
        self._after_write_open_confirm(room_id, shared, patch_id="", targets=targets)
        self.bus.emit_verdict(room_id, "R3", **payload)
        self.store.upsert(room)
        return room

    def _cmd_merge_conflict(self, room_id: str, payload: dict) -> Room:
        room = self._require(room_id)
        target = str(payload.get("target") or "").strip()
        strategy = str(payload.get("strategy") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not target or not strategy:
            raise ValueError("MergeConflict 缺 target/strategy")
        pending = [p for p in self.review.pending(room_id) if p.target == target]
        if len(pending) < 2 and strategy not in ("rewrite", "chooseA", "chooseB"):
            raise ValueError("MergeConflict 需要同块 ≥2 补丁（rewrite/choose 除外）")
        report = classify_target_conflict(pending) if len(pending) >= 2 else None
        if report and report.kind != "compatible_stack" and not reason:
            raise ValueError("不兼容/待裁定合并必须填写 reason")
        if strategy in ("chooseA", "chooseB", "rewrite", "concat") and not reason:
            # 策略表：均要求 reason
            raise ValueError("MergeConflict 必填 reason")

        final_text = ""
        mode = "choose"
        patch_ids = [p.patch_id for p in pending]
        if strategy == "rewrite":
            final_text = str(payload.get("newText") or payload.get("new_text") or "")
            if not final_text:
                raise ValueError("rewrite 缺 newText")
            mode = "rewrite"
        elif strategy in ("chooseA", "chooseB"):
            pid = str(payload.get("patch_id") or payload.get("patchId") or "")
            item = next((p for p in pending if p.patch_id == pid), None)
            if not item:
                raise ValueError("choose 缺有效 patchId")
            final_text = item.replace
            mode = "choose"
            patch_ids = [pid]
        elif strategy == "concat":
            order = payload.get("order") or payload.get("patchIds") or patch_ids
            order = [str(x) for x in order]
            final_text = stack_replaces(pending, order=order)
            mode = "concat" if report and report.kind == "compatible_stack" else "rewrite"
        else:
            raise ValueError(f"未知 strategy: {strategy}")

        if room.phase == "ReviewOpen":
            self.orch.transition(room_id, "AwaitingJudge", reason="MergeConflict")
        ok_t, treason = self.docs.can_patch_target(room_id, target)
        if not ok_t:
            raise ValueError(treason)
        shared = self.docs.apply_replace(room_id, target, final_text, bump=True)
        # 移出已合并补丁
        for p in list(self.review.pending(room_id)):
            if p.target == target:
                self.review.queue.dequeue(room_id, p.patch_id)
        rec = make_merge_record(
            room_id=room_id,
            target=target,
            mode=mode,  # type: ignore[arg-type]
            reason=reason,
            patch_ids=patch_ids,
            final_text=final_text,
            strategy=strategy,
        )
        self.judge.record_merge(rec)
        self.judge.note_block_author(
            room_id, target, str(payload.get("agent_id") or room.judge_agent_id or "")
        )
        self.judge.set_changed_set(room_id, [target])
        self.review.set_changed_set(room_id, {target})
        closed = self.judge.close_r1_for_target(room_id, target)
        self._after_write_open_confirm(
            room_id, shared, patch_id=rec.record_id, targets=[target]
        )
        self.bus.emit_verdict(room_id, "Merge", **payload)
        self.timeline.append(
            room_id,
            "MergeRecord",
            f"id={rec.record_id} mode={rec.mode} reason={reason[:40]}",
        )
        if closed:
            self.timeline.append(room_id, "R1Closed", f"target={target}")
        self.store.upsert(room)
        self._after_mutation(
            room_id,
            audit_type="Merge",
            payload={
                "target": target,
                "strategy": strategy,
                "reason": reason,
                "record_id": rec.record_id,
            },
        )
        return room

    def _cmd_accept_or_merge(
        self, room_id: str, command: str, payload: dict
    ) -> Room:
        room = self._require(room_id)
        if room.phase == "ReviewOpen":
            tok, tmsg = self.orch.transition(
                room_id, "AwaitingJudge", reason=command
            )
            if not tok:
                raise ValueError(tmsg)

        patch_id = str(payload.get("patch_id") or payload.get("patchId") or "")
        target = payload.get("target")
        replace = payload.get("replace")
        item = None
        if patch_id:
            item = self.review.queue.get(room_id, patch_id)
            if item:
                target = item.target
                replace = item.replace

        # 同块多补丁：禁止静默 Accept 单条（须 MergeConflict），除非显式强制
        if item and not payload.get("force_single"):
            siblings = [p for p in self.review.pending(room_id) if p.target == item.target]
            if len(siblings) >= 2:
                report = classify_target_conflict(siblings)
                if report.kind == "compatible_stack" and payload.get("stack"):
                    # 一键叠合预览后仍走合入
                    replace = stack_replaces(siblings)
                    target = item.target
                    for p in siblings:
                        if p.patch_id != patch_id:
                            self.review.queue.dequeue(room_id, p.patch_id)
                    rec = make_merge_record(
                        room_id=room_id,
                        target=str(target),
                        mode="stack",
                        reason=str(payload.get("reason") or "兼容可叠一键合入"),
                        patch_ids=[p.patch_id for p in siblings],
                        final_text=str(replace),
                        strategy="concat",
                    )
                    self.judge.record_merge(rec)
                elif report.kind != "compatible_stack":
                    raise ValueError(
                        f"同块冲突({report.kind})：请使用 MergeConflict 并填写 reason"
                    )

        if command == "Accept" and target and replace is not None:
            ok_t, reason = self.docs.can_patch_target(room_id, str(target))
            if not ok_t:
                raise ValueError(reason)
            shared = self.docs.apply_replace(
                room_id, str(target), str(replace), bump=True
            )
            if patch_id:
                self.review.queue.dequeue(room_id, patch_id)
            author = (item.agent_id if item else "") or str(
                payload.get("agent_id") or ""
            )
            if author:
                self.judge.note_block_author(room_id, str(target), author)
            closed = self.judge.close_r1_for_target(room_id, str(target))
            self.judge.set_changed_set(room_id, [str(target)])
            self.review.set_changed_set(room_id, {str(target)})
            self._after_write_open_confirm(
                room_id, shared, patch_id=patch_id, targets=[str(target)]
            )
            self.bus.emit_verdict(room_id, "Accept", **payload)
            self.bus.publish(
                room_id,
                "PatchAccepted",
                {"patch_id": patch_id, "target": target},
            )
            if closed:
                self.timeline.append(room_id, "R1Closed", f"target={target}")
            self.store.upsert(room)
            self._after_mutation(
                room_id,
                audit_type="Accept",
                payload={
                    "patch_id": patch_id,
                    "target": target,
                    "version": shared.version,
                },
            )
            return room

        # Merge 无具体文本：仅 bump（兼容旧壳）
        shared = self.docs.bump_version(room_id, base_from="merge")
        self._after_write_open_confirm(room_id, shared, patch_id=patch_id, targets=[])
        self.bus.emit_verdict(room_id, command, **payload)
        self.store.upsert(room)
        self._after_mutation(
            room_id, audit_type="Merge", payload={"patch_id": patch_id}
        )
        return room

    def _after_write_open_confirm(
        self,
        room_id: str,
        shared: Any,
        *,
        patch_id: str,
        targets: list[str],
    ) -> None:
        """合入/R3/R1关闭后：DocVersion + 强制开确认轮（不可跳过）。"""
        if ALLOW_SKIP_CONFIRM:
            raise RuntimeError("禁止跳过确认轮")
        room = self._require(room_id)
        self.confirm.note_write(room_id, targets)
        self.judge.set_changed_set(room_id, list(targets))
        self.review.set_changed_set(room_id, set(targets) if targets else set())
        self.bus.emit_after_merge(
            room_id,
            doc_version=shared.version,
            patch_id=patch_id,
            queue=[p.patch_id for p in self.review.pending(room_id)],
            block_ids=[b.block_id for b in shared.active_blocks()],
        )
        # AwaitingJudge → ConfirmOpen
        if room.phase == "AwaitingJudge":
            ok, msg = self.orch.open_confirm(room_id)
            if not ok:
                self.timeline.append(room_id, "BudgetStop", msg)
        elif room.phase == "ConfirmOpen":
            ok, msg = self.orch.open_confirm(room_id)
            if not ok:
                self.timeline.append(room_id, "BudgetStop", msg)
        idx = self.orch.require(room_id).budget.confirm_index
        cok, cmsg = self.confirm.open_round(room_id, targets, index=idx)
        if not cok:
            self.timeline.append(room_id, "ConfirmCapped", cmsg)
        room.review_window_open = True
        self.review.open_window(
            room_id,
            shared.version,
            timeout_ms=int(room.review_seconds * 1000),
        )
        if targets:
            self.timeline.append(
                room_id, "ChangedSet", ",".join(targets), collapsed=False
            )

    def publish_tool_receipt(self, room_id: str, **kwargs: Any) -> BusEvent:
        """T-BUS-04：工具回执进总线。"""
        self._require(room_id)
        return self.bus.publish_tool_receipt(room_id, **kwargs)

    def record_silent_check_pass(self, room_id: str, **payload: Any) -> BusEvent:
        self._require(room_id)
        return self.bus.publish(room_id, "SilentCheckPass", payload or {})

    def record_read(self, room_id: str, *, agent_id: str = "") -> BusEvent:
        self._require(room_id)
        if agent_id and self.docs.store.get_active(room_id):
            self.docs.register_read(room_id, agent_id)
        if agent_id:
            self.review.register_read(room_id, agent_id)
            self.confirm.mark_read(room_id, agent_id)
        return self.bus.publish(room_id, "Read", {"agent_id": agent_id})

    def ingest_worker_output(
        self,
        room_id: str,
        agent_id: str,
        text: str,
    ) -> NormalizeResult:
        """T-PROTO：工人自由文本 → 结构消息；唯一上队列/总线闸门。"""
        room = self._require(room_id)
        doc = self.get_doc(room_id)
        result = self.proto.normalize_worker(
            room_id=room_id,
            agent_id=agent_id,
            text=text,
            doc_version=doc.doc_version,
            doc_id=f"{room_id}:V{doc.doc_version}",
        )
        if result.ok and result.message:
            if result.message.kind == "Patch":
                ok_f, msg_f = self._apply_m5_patch(
                    room_id, agent_id, dict(result.message.fields)
                )
                from multi_agent_room.protocol import NormalizeResult as NR

                return NR(
                    ok=True,
                    route="ready" if ok_f else "reject",
                    message=result.message,
                    entered_queue=ok_f,
                    error="" if ok_f else msg_f,
                )
            self._emit_proto_ready(room_id, result.message)
        return result

    def ingest_judge_output(
        self,
        room_id: str,
        agent_id: str,
        text: str,
    ) -> NormalizeResult:
        """T-PROTO：评议自由文本 → Accept/Merge/R*/JudgeApprove。"""
        self._require(room_id)
        result = self.proto.normalize_judge(
            room_id=room_id, agent_id=agent_id, text=text
        )
        if result.ok and result.message:
            kind = result.message.kind
            if kind == "JudgeApprove":
                allowed, reason = self.agents.roles.can_judge_approve(room_id, agent_id)
                if not allowed:
                    self.agents.thoughts.write(
                        agent_id, text, tag="proto_reject"
                    )
                    from multi_agent_room.protocol import NormalizeResult as NR

                    return NR(
                        ok=False,
                        route="reject",
                        error=reason,
                        entered_queue=False,
                    )
                try:
                    self.mark_gate_passed_demo(room_id, agent_id=agent_id)
                except (PermissionError, ValueError) as exc:
                    from multi_agent_room.protocol import NormalizeResult as NR

                    return NR(
                        ok=False,
                        route="reject",
                        error=str(exc),
                        entered_queue=False,
                    )
            elif kind in ("Accept", "Merge", "R1", "R2", "R3"):
                try:
                    self.judge_command(room_id, kind, **result.message.fields)
                except (ValueError, PermissionError) as exc:
                    from multi_agent_room.protocol import NormalizeResult as NR

                    return NR(
                        ok=False,
                        route="reject",
                        error=str(exc),
                        message=result.message,
                        entered_queue=False,
                    )
            else:
                self._emit_proto_ready(room_id, result.message)
        return result

    def pending_patches(self, room_id: str) -> list:
        """权威待合入：M5 队列（Filter 通过后）。"""
        return self.review.pending(room_id)

    def _apply_m5_patch(self, room_id: str, agent_id: str, fields: dict) -> tuple[bool, str]:
        """M5 过滤 → 入队；确认轮内须 target∈ChangedSet。"""
        target = str(fields.get("target") or "")
        ok_t, reason = self.docs.can_patch_target(room_id, target or None)
        if not ok_t:
            self._drop_proto_patch(room_id, fields)
            self.timeline.append(room_id, "PatchReject", f"invalid:{reason}")
            return False, reason
        # R1 未关闭：仅允许对已打回 target 重交；未点名块锁定
        open_rej = self.judge.list_open_rejects(room_id)
        if open_rej:
            allowed_r1 = {r.target for r in open_rej}
            if target not in allowed_r1:
                msg = f"R1 未点名块锁定: {target}（仅可重交 {sorted(allowed_r1)}）"
                self._drop_proto_patch(room_id, fields)
                self.timeline.append(room_id, "PatchReject", f"r1_lock:{msg}")
                return False, msg
        # 确认轮 ChangedSet；开打回 target 重交例外（可在 ConfirmOpen 继续关 R1）
        r1_resubmit = any(r.target == target for r in open_rej)
        if not r1_resubmit:
            ok_s, sreason = self.confirm.assert_patch_in_scope(room_id, target)
            if not ok_s:
                self._drop_proto_patch(room_id, fields)
                self.timeline.append(room_id, "PatchReject", f"out_of_scope:{sreason}")
                return False, sreason
        doc = self.docs.store.get_active(room_id)
        assert doc is not None
        blk = doc.get_block(target)
        old = blk.text if blk else ""
        others = {
            b.block_id: b.text
            for b in doc.active_blocks()
            if b.block_id != target
        }
        result = self.review.submit_patch(
            room_id=room_id,
            agent_id=agent_id,
            fields=fields,
            old_text=old,
            doc_full=doc.full_text(),
            doc_version=doc.version,
            other_block_texts=others,
            skip_changed_set=r1_resubmit,
        )
        if not result.ok:
            self._drop_proto_patch(room_id, fields)
            self.timeline.append(
                room_id,
                "PatchReject",
                f"{result.code}:{result.message}",
                collapsed=False,
            )
            return False, f"{result.code}:{result.message}"
        assert result.patch is not None
        fields["patch_id"] = result.patch.patch_id
        self.docs.register_read(room_id, agent_id)
        self.record_patch(room_id)
        self.timeline.append(
            room_id,
            "PatchQueued",
            f"id={result.patch.patch_id} target={result.patch.target} "
            f"claim={result.patch.claim[:40]}",
            collapsed=False,
        )
        self.bus.publish(
            room_id,
            "QueueUpdated",
            {"patch_id": result.patch.patch_id, "target": result.patch.target},
        )
        self._after_mutation(
            room_id,
            audit_type="PatchQueued",
            payload={
                "patch_id": result.patch.patch_id,
                "target": result.patch.target,
            },
        )
        return True, "ok"

    def _drop_proto_patch(self, room_id: str, fields: dict) -> None:
        q = self.proto._patch_queue.get(room_id) or []
        self.proto._patch_queue[room_id] = [
            m
            for m in q
            if not (
                m.fields.get("target") == fields.get("target")
                and m.fields.get("claim") == fields.get("claim")
                and m.fields.get("replace") == fields.get("replace")
            )
        ]

    def _emit_proto_ready(self, room_id: str, msg: Any) -> None:
        kind = msg.kind
        fields = dict(msg.fields)
        if kind == "Read":
            aid = str(fields.get("agent_id") or "")
            if aid and self.docs.store.get_active(room_id):
                self.docs.register_read(room_id, aid)
            if aid:
                self.review.register_read(room_id, aid)
            self.bus.publish(room_id, "Read", fields)
        elif kind == "SilentCheckPass":
            self.bus.publish(room_id, "SilentCheckPass", fields)
        elif kind == "Clarify":
            q = str(fields.get("question") or "")
            if q:
                self.ask_clarify(room_id, q, from_agent=str(fields.get("agent_id") or ""))
        elif kind == "Abstain":
            aid = str(fields.get("agent_id") or "")
            if aid:
                self.review.register_abstain(room_id, aid)
            self.timeline.append(room_id, "Abstain", f"agent={aid}")
        elif kind == "Patch":
            # 由 ingest_worker_output 直接走 M5，避免重复
            pass

    def record_patch(self, room_id: str) -> tuple[bool, str]:
        """登记实质补丁（供 M5 调用）；超预算升用户。"""
        room = self._require(room_id)
        self.orch.ensure_bound(room)
        ok, msg = self.orch.record_patch(room_id)
        room.escalation_hint = self.orch.require(room_id).escalation_hint
        self.store.upsert(room)
        if not ok:
            self.timeline.append(room_id, "BudgetStop", msg)
        return ok, msg

    # ---- 工作区（T-M2-08）----
    def set_workspace(self, room_id: str, path: str | Path) -> Room:
        room = self._require(room_id)
        normalized = ensure_workspace_dir(path)
        room.workspace_path = str(normalized)
        self.store.upsert(room)
        cfg = load_config()
        cfg.bind_room_workspace(room_id, normalized)
        save_config(cfg)
        self.timeline.append(room_id, "WorkspaceBound", str(normalized))
        log_event("workspace_bound", f"path={normalized}", room_id=room_id)
        return room

    def assert_path_in_workspace(self, room_id: str, target: str | Path) -> Path:
        """越界写盘失败（P0 约束，不依赖 M12）。"""
        room = self._require(room_id)
        if not room.workspace_path:
            raise ValueError("房间未绑定工作区")
        root = normalize_workspace_path(room.workspace_path)
        target_p = normalize_workspace_path(target)
        try:
            target_p.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"越界写盘拒绝: {target_p} 不在 {root}") from exc
        return target_p

    # ---- 内部 ----
    # ---- T-M9：共享 / 私有记忆 ----
    def write_shared_memory(
        self,
        room_id: str,
        kind: SharedKind,
        content: str,
        **kwargs: Any,
    ):
        room = self._require(room_id)
        kwargs.setdefault("gate_passed", bool(room.gate_passed))
        return self.memory.write_shared(room_id, kind, content, **kwargs)

    def write_private_memory(
        self, room_id: str, agent_id: str, content: str, **kwargs: Any
    ):
        self._require(room_id)
        return self.memory.write_private(agent_id, room_id, content, **kwargs)

    def search_shared_memory(
        self,
        room_id: str,
        *,
        query: str = "",
        kind: Optional[SharedKind] = None,
        viewer_agent_id: Optional[str] = None,
    ):
        self._require(room_id)
        return self.memory.search_shared(
            room_id, query=query, kind=kind, viewer_agent_id=viewer_agent_id
        )

    def search_private_memory(
        self,
        viewer_agent_id: str,
        *,
        room_id: str = "",
        query: str = "",
        target_agent_id: Optional[str] = None,
    ):
        return self.memory.search_private(
            viewer_agent_id,
            room_id=room_id,
            query=query,
            target_agent_id=target_agent_id,
        )

    def agent_memory_context(self, viewer_agent_id: str, room_id: str) -> dict[str, Any]:
        """合并思考区 + 私有记忆；他模私有内容为空。"""
        room = self._require(room_id)
        peer_ids = list(room.invited_agent_ids) or list(
            self.agents.roles.get_or_create(room_id).invited_agent_ids
        )
        thought_ctx = self.agents.context_for(viewer_agent_id, room_id)
        mem_ctx = self.memory.assemble_private_context(
            viewer_agent_id, peer_ids, room_id=room_id
        )
        shared_hits = [
            i.to_dict()
            for i in self.memory.search_shared(
                room_id, viewer_agent_id=viewer_agent_id
            )
        ]
        return {
            **thought_ctx,
            **mem_ctx,
            "shared_memory": shared_hits,
        }

    # ---- T-M10：Skills / 本机工具 ----
    def _tool_ctx(
        self, room_id: str, *, agent_id: Optional[str] = None
    ) -> RoomToolContext:
        room = self._require(room_id)
        return RoomToolContext(
            room_id=room_id,
            phase=room.phase,
            workspace_path=room.workspace_path,
            gate_passed=bool(room.gate_passed),
            write_token=room.write_token,
            agent_id=agent_id,
        )

    def authorize_skill(
        self,
        skill_id: str,
        *,
        room_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        return self.tools.skills.authorize(
            skill_id, room_id=room_id, agent_id=agent_id
        )

    def invoke_skill(
        self,
        room_id: str,
        skill_id: str,
        args: Optional[dict[str, Any]] = None,
        *,
        agent_id: Optional[str] = None,
        user_confirmed: bool = False,
    ) -> ToolResult:
        ctx = self._tool_ctx(room_id, agent_id=agent_id)
        return self.tools.invoke_skill(
            skill_id,
            args or {},
            ctx,
            user_confirmed=user_confirmed,
            timeline_append=self.timeline.append,
        )

    def confirm_high_risk_tool(
        self, room_id: str, skill_id: str, *, agent_id: Optional[str] = None
    ) -> ToolResult:
        ctx = self._tool_ctx(room_id, agent_id=agent_id)
        return self.tools.confirm_and_invoke(
            skill_id, ctx, timeline_append=self.timeline.append
        )

    def authorize_deliver(
        self,
        room_id: str,
        *,
        scope: str = "workspace_write",
        ttl_sec: float = 3600,
        **_extra: Any,
    ) -> Room:
        """评判台发放 writeToken（不自动 Final）；审计必记。"""
        room = self._require(room_id)
        room.write_token = {
            "exp": time.time() + float(ttl_sec),
            "scope": scope,
            "granted_at": time.time(),
        }
        self.store.upsert(room)
        self.audit.append(
            room_id,
            "AuthorizeDeliver",
            {"scope": scope, "exp": room.write_token["exp"]},
        )
        self.timeline.append(room_id, "AuthorizeDeliver", f"scope={scope}")
        log_event("authorize_deliver", f"scope={scope}", room_id=room_id)
        return room

    def click_deliver(
        self,
        room_id: str,
        *,
        content: Optional[str] = None,
        summary: Optional[str] = None,
        rel_dir: str = "delivery",
        filename: str = "final-reply.md",
        extra_files: Optional[dict[str, str]] = None,
        force_path: Optional[str] = None,
    ) -> DeliverResult:
        """T1：用户点「交付」——须 FinalCommitted 或 writeToken；不靠关键词。"""
        room = self._require(room_id)
        result = self.deliver_svc.deliver(
            room,
            content=content,
            summary=summary,
            rel_dir=rel_dir,
            filename=filename,
            extra_files=extra_files,
            force_path=force_path,
        )
        self._last_delivery[room_id] = result
        # 回执进房（总线 ToolReceipt + 时间线 DeliveryReceipt）
        paths = [i.abs_path for i in result.items]
        self.bus.publish_tool_receipt(
            room_id,
            tool_name="deliver",
            ok=result.ok,
            paths=paths,
            diff_summary=result.manifest_rel or result.message,
            code=result.code,
            message=result.message,
            delivery_id=result.delivery_id,
            gate=result.gate,
        )
        self.timeline.append(
            room_id,
            "DeliveryReceipt",
            f"ok={result.ok} {result.code} n={len(result.items)}",
            delivery_id=result.delivery_id,
            gate=result.gate,
            manifest=result.manifest_rel,
        )
        if result.ok:
            self.audit.append(
                room_id,
                "DeliveryReceipt",
                result.to_dict(),
            )
            try:
                self.memory.write_shared(
                    room_id,
                    "delivery_index",
                    result.manifest_rel or result.delivery_id,
                    tags=["delivery"],
                    resolved=True,
                    gate_passed=True,
                    meta={
                        "delivery_id": result.delivery_id,
                        "paths": [i.rel_path for i in result.items],
                        "gate": result.gate,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log_event("delivery_index_skip", str(exc), room_id=room_id)
        else:
            log_event(
                "deliver_deny",
                f"{result.code}: {result.message}",
                room_id=room_id,
            )
        return result

    def last_delivery(self, room_id: str) -> Optional[DeliverResult]:
        return self._last_delivery.get(room_id)

    def verify_delivery_manifest(self, room_id: str) -> tuple[bool, str]:
        room = self._require(room_id)
        last = self._last_delivery.get(room_id)
        if not last or not last.ok or not last.manifest_rel:
            return False, "无成功交付清单"
        if not room.workspace_path:
            return False, "无工作区"
        return self.deliver_svc.verify_manifest_on_disk(
            room.workspace_path, last.manifest_rel
        )

    # ---- T-M11：执行补充 ----
    def run_exec_t0(self, room_id: str) -> ExecRunResult:
        room = self._require(room_id)
        result = self.exec_svc.run_t0(room)
        self.timeline.append(room_id, "ExecT0", result.message)
        return result

    def run_exec_t2(
        self,
        room_id: str,
        source: str,
        *,
        filename: str = "main.py",
        goal: str = "",
        extra_files: Optional[dict[str, str]] = None,
    ) -> ExecRunResult:
        room = self._require(room_id)
        result = self.exec_svc.run_t2(
            room,
            source,
            filename=filename,
            goal=goal,
            extra_files=extra_files,
        )
        self.timeline.append(
            room_id,
            "ExecT2",
            f"ok={result.ok} code={result.code}",
        )
        if result.feedback:
            self.timeline.append(
                room_id,
                "SandboxFeedback",
                result.feedback.prompt_text()[:240],
            )
            self.bus.publish_tool_receipt(
                room_id,
                tool_name="sandbox",
                ok=False,
                exit_code=result.sandbox.exit_code if result.sandbox else None,
                diff_summary=result.feedback.error_type,
                message=result.feedback.message,
                stack=(result.feedback.stack or "")[:4000],
            )
        elif result.sandbox:
            self.bus.publish_tool_receipt(
                room_id,
                tool_name="sandbox",
                ok=result.sandbox.ok,
                exit_code=result.sandbox.exit_code,
                paths=result.sandbox.related_files,
                message=result.sandbox.message,
            )
        return result

    def mark_task_complete(self, room_id: str) -> tuple[bool, str]:
        """须已有 M7 FinalCommitted；禁止跳过门禁标完成。"""
        room = self._require(room_id)
        ok, msg = self.exec_svc.mark_complete(room)
        self.timeline.append(
            room_id,
            "TaskComplete",
            f"ok={ok} {msg}",
        )
        if not ok:
            log_event("task_complete_deny", msg, room_id=room_id)
        return ok, msg

    def review_exec_result(
        self, *, goal: str, current_result: str, gap: str = ""
    ):
        from multi_agent_room.exec_service import review_result

        return review_result(
            goal=goal, current_result=current_result, gap=gap
        )

    # ---- T-M8b：审计回放 / RoomState 恢复 ----
    def capture_room_state(self, room_id: str) -> RoomState:
        room = self._require(room_id)
        doc = self.docs.store.get_active(room_id)
        doc_d = None
        if doc:
            doc_d = {
                "docId": doc.doc_id,
                "version": doc.version,
                "status": doc.status,
                "blocks": [b.to_dict() for b in doc.blocks],
                "tombstones": list(doc.tombstones),
                "baseFrom": doc.base_from,
                "authorAgentId": doc.author_agent_id,
            }
        roles = self.agents.roles.get_or_create(room_id).roles
        conf = self.confirm.get(room_id)
        return RoomState(
            room_id=room_id,
            phase=room.phase,
            frozen=room.frozen,
            clarify_hold=room.clarify_hold,
            doc=doc_d,
            queue=self.review.pending_as_dicts(room_id),
            open_rejects=[
                {
                    "reject_id": r.reject_id,
                    "target": r.target,
                    "reason": r.reason,
                    "assignee_agent_id": r.assignee_agent_id,
                    "closed": r.closed,
                }
                for r in self.judge.open_rejects.get(room_id, [])
            ],
            confirm={
                "index": conf.confirm_index,
                "changedSet": sorted(conf.changed_set),
                "active": conf.active,
                "hadWrite": conf.had_write,
                "markedClean": conf.marked_clean,
                "maxConfirmChurn": conf.max_confirm_churn,
            },
            roles={
                "first_answerer_agent_id": roles.first_answerer_agent_id,
                "first_answerer_config_id": roles.first_answerer_config_id,
                "reviewer_agent_ids": list(roles.reviewer_agent_ids),
                "judge_agent_id": roles.judge_agent_id,
                "judge_is_user": roles.judge_is_user,
                "frozen": roles.frozen,
            },
            room=room.to_dict(),
        )

    def persist_room(self, room_id: str) -> RoomState:
        state = self.capture_room_state(room_id)
        self.state_store.save(state)
        return state

    def restore_room(self, room_id: str, state: Optional[RoomState] = None) -> Room:
        """从磁盘 RoomState 恢复：phase/doc/queue/rejects/confirm/roles。"""
        st = state or self.state_store.load(room_id)
        if not st:
            raise FileNotFoundError(f"无 RoomState: {room_id}")
        raw_room = dict(st.room or {})
        raw_room.setdefault("room_id", room_id)
        raw_room["phase"] = st.phase
        raw_room["frozen"] = st.frozen
        raw_room["clarify_hold"] = st.clarify_hold
        room = Room.from_dict(raw_room)
        self.store.upsert(room)
        self.orch.bind(room)

        # 文档
        self.docs.store._active.pop(room_id, None)
        if st.doc:
            blocks = [
                DocBlock(
                    block_id=str(b.get("block_id") or b.get("blockId") or ""),
                    text=str(b.get("text") or ""),
                    order=int(b.get("order") or 0),
                    block_type=str(b.get("block_type") or b.get("blockType") or "text"),
                    version=int(b.get("version") or 1),
                    split_from=b.get("split_from") or b.get("splitFrom"),
                    tombstoned=bool(b.get("tombstoned", False)),
                )
                for b in (st.doc.get("blocks") or [])
            ]
            doc = SharedDoc(
                doc_id=str(st.doc.get("docId") or st.doc.get("doc_id") or ""),
                room_id=room_id,
                version=int(st.doc.get("version") or 0),
                status=st.doc.get("status") or "active",  # type: ignore[arg-type]
                blocks=blocks,
                tombstones=list(st.doc.get("tombstones") or []),
                base_from=st.doc.get("baseFrom") or st.doc.get("base_from") or "firstAnswer",  # type: ignore[arg-type]
                author_agent_id=str(
                    st.doc.get("authorAgentId") or st.doc.get("author_agent_id") or ""
                ),
            )
            self.docs.store._active[room_id] = doc

        # 队列
        self.review.queue._q[room_id] = []
        for q in st.queue:
            self.review.queue.enqueue(
                PatchItem(
                    patch_id=str(q.get("patch_id") or q.get("patchId") or ""),
                    room_id=room_id,
                    agent_id=str(q.get("agent_id") or ""),
                    target=str(q.get("target") or ""),
                    category=str(q.get("category") or ""),
                    claim=str(q.get("claim") or ""),
                    replace=str(q.get("replace") or ""),
                    version=int(q.get("version") or 0),
                )
            )

        # OpenReject
        self.judge.open_rejects[room_id] = [
            OpenReject(
                reject_id=str(r.get("reject_id") or ""),
                room_id=room_id,
                target=str(r.get("target") or ""),
                reason=str(r.get("reason") or ""),
                assignee_agent_id=str(r.get("assignee_agent_id") or ""),
                closed=bool(r.get("closed", False)),
            )
            for r in st.open_rejects
        ]

        # Confirm
        conf = self.confirm.get(room_id)
        c = st.confirm or {}
        conf.confirm_index = int(c.get("index") or 0)
        conf.changed_set = set(c.get("changedSet") or c.get("changed_set") or [])
        conf.active = bool(c.get("active", False))
        conf.had_write = bool(c.get("hadWrite", c.get("had_write", False)))
        conf.marked_clean = bool(c.get("markedClean", c.get("marked_clean", False)))
        conf.max_confirm_churn = int(
            c.get("maxConfirmChurn") or c.get("max_confirm_churn") or 3
        )
        self.review.set_changed_set(
            room_id, set(conf.changed_set) if conf.active else None
        )
        self.judge.set_changed_set(room_id, list(conf.changed_set))

        # Roles
        rs = self.agents.roles.get_or_create(room_id)
        rd = st.roles or {}
        rs.roles.first_answerer_agent_id = rd.get("first_answerer_agent_id")
        rs.roles.first_answerer_config_id = rd.get("first_answerer_config_id")
        rs.roles.reviewer_agent_ids = list(rd.get("reviewer_agent_ids") or [])
        rs.roles.judge_agent_id = rd.get("judge_agent_id")
        rs.roles.judge_is_user = bool(rd.get("judge_is_user", False))
        rs.roles.frozen = bool(rd.get("frozen", False))
        rs.invited_agent_ids = list(room.invited_agent_ids)
        rs.user_question = room.pinned_question

        self._docs[room_id] = SharedDocView(room_id=room_id, _svc=self.docs)
        self._ensure_bus_mirror(room_id)
        log_event("room_restore", f"phase={room.phase} V={st.doc and st.doc.get('version')}", room_id=room_id)
        return room

    def replay_audit(
        self, room_id: str, *, types: Optional[list[str]] = None
    ) -> list:
        """回放审计事件（供 UI / 验收）。"""
        return self.audit.replay(room_id, types=types)

    def archive_room(self, room_id: str) -> Path:
        """P1a 最小归档。"""
        self.persist_room(room_id)
        return self.state_store.archive(room_id)

    def list_archived_rooms(self) -> list[dict[str, Any]]:
        return self.state_store.list_archived()

    def _after_mutation(
        self,
        room_id: str,
        *,
        audit_type: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        if audit_type:
            self.audit.append(room_id, audit_type, payload or {})
        try:
            self.persist_room(room_id)
        except Exception as exc:  # noqa: BLE001 — 持久化失败不阻断主流程
            log_event("persist_fail", str(exc), room_id=room_id)

    def _require(self, room_id: str) -> Room:
        room = self.store.get(room_id)
        if not room:
            raise KeyError(f"房间不存在: {room_id}")
        return room

    def _ensure_bus_mirror(self, room_id: str) -> None:
        """将总线事件镜像到 M2 时间线（UI）；订阅按 room 隔离。"""
        if room_id in self._bus_mirrors:
            return

        def _on_ev(ev: BusEvent) -> None:
            summary = ev.type
            if ev.payload:
                summary = f"{ev.type} { {k: ev.payload[k] for k in list(ev.payload)[:3]} }"
            self.timeline.append(
                ev.room_id,
                ev.type,
                summary[:160],
                collapsed=ev.type in ("RoomIdle", "RoomAwake"),
                seq=ev.seq,
                bus_id=ev.id,
            )

        self._bus_mirrors[room_id] = self.bus.subscribe(room_id, _on_ev)

    def _begin_pause(self, room_id: str) -> None:
        self._pause_started[room_id] = time.time()

    def _end_pause(self, room: Room) -> None:
        started = self._pause_started.pop(room.room_id, None)
        if started is None:
            return
        paused = max(0.0, time.time() - started)
        if room.review_deadline_ts > 0:
            room.review_deadline_ts += paused
