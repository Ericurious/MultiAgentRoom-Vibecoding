"""T-M6 验收：M6-A～M6-J。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_room.adapters import ProbeResult  # noqa: E402
from multi_agent_room.agent_service import AgentService  # noqa: E402
from multi_agent_room.conflict import classify_target_conflict  # noqa: E402
from multi_agent_room.event_bus import EventBus  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.patch_filter import PatchItem  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402


class M6Tests(unittest.TestCase):
    def setUp(self) -> None:
        setup_logging()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._env = patch.dict("os.environ", {"APPDATA": self._tmpdir.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        import multi_agent_room.secret_store as ss

        ss._STORE = None
        self.bus = EventBus(persist=False)
        self.models = ModelService()
        self.agents = AgentService(models=self.models)
        self.svc = RoomService(
            agents=self.agents, models=self.models, bus=self.bus
        )
        self.cfg_w = self.models.add_model(
            display_name="W",
            base_url="https://w.example/v1",
            model_id="w",
            api_key="sk-w",
        )
        self.cfg_j = self.models.add_model(
            display_name="J",
            base_url="https://j.example/v1",
            model_id="j",
            api_key="sk-j",
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "ok"),
        ):
            self.models.probe(self.cfg_w.config_id)
            self.models.probe(self.cfg_j.config_id)
        self.worker = self.agents.create_agent(
            display_name="Worker",
            model_config_id=self.cfg_w.config_id,
            agent_id="agent-w",
        )
        self.judge = self.agents.create_agent(
            display_name="Judge",
            model_config_id=self.cfg_j.config_id,
            agent_id="agent-j",
        )
        self.room = self.svc.create_room("M6房")
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.svc.invite_ready_agent(self.room.room_id, self.judge.agent_id)
        self.rid = self.room.room_id

    def _open(self, text: str = "原始块正文内容。\n") -> str:
        self.svc.ask_question(self.rid, "请完成任务并给出可执行方案")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_agent_id=self.judge.agent_id,
        )
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, text)
        doc = self.svc.docs.store.get_active(self.rid)
        assert doc is not None
        return doc.active_blocks()[0].block_id

    def _enqueue(
        self, target: str, replace: str, claim: str = "缺错误处理会导致崩溃", **kw
    ) -> str:
        fields = {
            "target": target,
            "category": kw.get("category", "logic"),
            "claim": claim,
            "replace": replace,
        }
        doc = self.svc.docs.require_active(self.rid)
        r = self.svc.review.submit_patch(
            room_id=self.rid,
            agent_id=kw.get("agent_id", self.worker.agent_id),
            fields=fields,
            old_text=doc.get_block(target).text if doc.get_block(target) else "",
            doc_full=doc.full_text(),
            doc_version=doc.version,
        )
        self.assertTrue(r.ok, msg=r.message)
        assert r.patch is not None
        return r.patch.patch_id

    def test_M6_A_no_accept_doc_unchanged(self) -> None:
        bid = self._open("不变稿\n")
        doc = self.svc.docs.require_active(self.rid)
        before = doc.get_block(bid).text
        ver = doc.version
        self._enqueue(bid, "被排队但未合入的文本，补充错误处理。")
        doc2 = self.svc.docs.require_active(self.rid)
        self.assertEqual(doc2.get_block(bid).text, before)
        self.assertEqual(doc2.version, ver)

    def test_M6_B_accept_bumps_and_confirm(self) -> None:
        bid = self._open("待合入原文\n")
        pid = self._enqueue(bid, "合入后的新文本，含错误处理。")
        ver = self.svc.docs.require_active(self.rid).version
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        doc = self.svc.docs.require_active(self.rid)
        self.assertEqual(doc.version, ver + 1)
        self.assertIn("合入后", doc.get_block(bid).text)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ConfirmOpen")
        self.assertEqual(self.svc.judge.get_changed_set(self.rid), [bid])

    def test_M6_C_merge_with_record(self) -> None:
        bid = self._open("冲突块原文足够长一些。\n")
        p1 = self._enqueue(
            bid,
            "方案 A：使用本地校验与超时控制。",
            claim="改控制流防止崩溃",
            agent_id=self.worker.agent_id,
        )
        p2 = self._enqueue(
            bid,
            "方案 B：使用远程校验与重试策略。",
            claim="改控制流防止崩溃",
            agent_id=self.judge.agent_id,
        )
        self.svc.judge_command(
            self.rid,
            "MergeConflict",
            target=bid,
            strategy="chooseA",
            patch_id=p1,
            reason="选 A 更贴合原话目标",
        )
        recs = self.svc.judge.list_merge_records(self.rid)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].reason, "选 A 更贴合原话目标")
        self.assertIn(p1, recs[0].candidate_patch_ids)
        doc = self.svc.docs.require_active(self.rid)
        self.assertIn("方案 A", doc.get_block(bid).text)

    def test_M6_C2_incompatible_needs_reason(self) -> None:
        bid = self._open("原文块\n")
        self._enqueue(bid, "整块替换文本甲甲甲。", claim="改控制流防止崩溃")
        self._enqueue(
            bid,
            "整块替换文本乙乙乙。",
            claim="改控制流防止崩溃",
            agent_id=self.judge.agent_id,
        )
        with self.assertRaises(ValueError) as ctx:
            self.svc.judge_command(
                self.rid,
                "MergeConflict",
                target=bid,
                strategy="rewrite",
                newText="手写合并稿",
                reason="",
            )
        self.assertIn("reason", str(ctx.exception))

    def test_M6_C3_compatible_stack_needs_accept(self) -> None:
        # 用等价 replace → compatible_stack
        bid = self._open("原文\n")
        a = "同一归一化结果文本"
        p1 = self._enqueue(bid, a, claim="改控制流防止崩溃")
        p2 = self._enqueue(
            bid, f"{a}  ", claim="改控制流防止崩溃", agent_id=self.judge.agent_id
        )
        items = [p for p in self.svc.review.pending(self.rid) if p.target == bid]
        report = classify_target_conflict(items)
        self.assertEqual(report.kind, "compatible_stack")
        before = self.svc.docs.require_active(self.rid).version
        # 未点合入前稿不变
        self.assertEqual(self.svc.docs.require_active(self.rid).version, before)
        self.svc.judge_command(
            self.rid, "Accept", patch_id=p1, stack=True, reason="叠合入队"
        )
        self.assertEqual(
            self.svc.docs.require_active(self.rid).version, before + 1
        )
        self.assertTrue(self.svc.judge.list_merge_records(self.rid))

    def test_M6_D_r1_blocks_final_then_close(self) -> None:
        bid = self._open("块\n")
        self.svc.judge_command(
            self.rid, "R1", target=bid, reason="缺边界检查"
        )
        self.assertTrue(self.svc.judge.has_open_rejects(self.rid))
        with self.assertRaises(ValueError):
            self.svc.judge_command(
                self.rid, "JudgeApprove", agent_id=self.judge.agent_id
            )
        # 重交合入 → 关闭 R1 → ConfirmOpen
        pid = self._enqueue(bid, "重交后补上边界检查。", claim="缺错误处理会导致崩溃")
        ver = self.svc.docs.require_active(self.rid).version
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        self.assertFalse(self.svc.judge.has_open_rejects(self.rid))
        self.assertEqual(self.svc.docs.require_active(self.rid).version, ver + 1)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ConfirmOpen")

    def test_M6_E_r2_missing_goal_ref(self) -> None:
        self._open()
        with self.assertRaises(ValueError) as ctx:
            self.svc.judge_command(
                self.rid,
                "R2",
                wrong_direction="偏了",
                required_direction="重做",
                keep=[],
            )
        self.assertIn("user_goal_ref", str(ctx.exception))

    def test_M6_F_r2_voids_old(self) -> None:
        bid = self._open("将被作废")
        old = self.svc.docs.require_active(self.rid).doc_id
        self.svc.judge_command(
            self.rid,
            "R2",
            user_goal_ref="请完成任务并给出可执行方案",
            wrong_direction="方向偏离用户目标",
            required_direction="按原话重写首答",
            keep=["无"],
            discard=["旧稿"],
        )
        self.assertIsNone(self.svc.docs.store.get_active(self.rid))
        hist = self.svc.docs.store._history.get(self.rid) or []
        self.assertTrue(any(d.doc_id == old and d.status == "voided" for d in hist))

    def test_M6_G_r3_out_of_bounds(self) -> None:
        bid = self._open("小疵\n")
        with self.assertRaises(ValueError) as ctx:
            self.svc.judge_command(
                self.rid,
                "R3",
                target=bid,
                replace="换架构为微服务并改对外 API 契约",
                claim="换架构",
            )
        self.assertIn("越界", str(ctx.exception))

    def test_M6_H_non_judge_approve_fails(self) -> None:
        self._open()
        with self.assertRaises(PermissionError):
            self.svc.judge_command(
                self.rid, "JudgeApprove", agent_id=self.worker.agent_id
            )

    def test_M6_I_context_no_chatter(self) -> None:
        self._open("正文")
        # 工人闲聊进私有区，不得进评议上下文
        self.svc.ingest_worker_output(
            self.rid, self.worker.agent_id, "你好啊，今天怎么样，随便聊聊"
        )
        secret = "闲聊秘密短语XYZ"
        self.agents.thoughts.write(self.worker.agent_id, secret, tag="chat")
        ctx = self.svc.judge_context(self.rid)
        blob = json.dumps(ctx, ensure_ascii=False)
        self.assertIn("请完成任务", blob)
        self.assertNotIn(secret, blob)
        self.assertNotIn("随便聊聊", blob)

    def test_M6_J_worker_judge_approve_fails(self) -> None:
        self._open()
        r = self.svc.ingest_judge_output(
            self.rid,
            self.worker.agent_id,
            json.dumps({"type": "JudgeApprove", "agent_id": self.worker.agent_id}),
        )
        self.assertFalse(r.ok)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertNotEqual(room.phase, "Final")


if __name__ == "__main__":
    unittest.main()
