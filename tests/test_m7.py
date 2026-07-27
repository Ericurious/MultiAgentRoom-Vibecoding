"""T-M7 验收：M7-A～M7-F。"""

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
from multi_agent_room.confirm_service import ALLOW_SKIP_CONFIRM  # noqa: E402
from multi_agent_room.event_bus import EventBus  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402


class M7Tests(unittest.TestCase):
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
            display_name="W",
            model_config_id=self.cfg_w.config_id,
            agent_id="agent-w",
        )
        self.judge = self.agents.create_agent(
            display_name="J",
            model_config_id=self.cfg_j.config_id,
            agent_id="agent-j",
        )
        self.room = self.svc.create_room("M7房")
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.svc.invite_ready_agent(self.room.room_id, self.judge.agent_id)
        self.rid = self.room.room_id
        self.assertFalse(ALLOW_SKIP_CONFIRM)

    def _roles(self) -> None:
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_agent_id=self.judge.agent_id,
            reviewer_agent_ids=[self.worker.agent_id],
        )

    def _open(self, text: str = "首答正文内容。\n") -> str:
        self.svc.ask_question(self.rid, "用户任务原话")
        self._roles()
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, text)
        doc = self.svc.docs.require_active(self.rid)
        return doc.active_blocks()[0].block_id

    def _enqueue(self, target: str, replace: str) -> str:
        doc = self.svc.docs.require_active(self.rid)
        r = self.svc.review.submit_patch(
            room_id=self.rid,
            agent_id=self.worker.agent_id,
            fields={
                "target": target,
                "category": "logic",
                "claim": "缺错误处理会导致崩溃",
                "replace": replace,
            },
            old_text=doc.get_block(target).text if doc.get_block(target) else "",
            doc_full=doc.full_text(),
            doc_version=doc.version,
        )
        self.assertTrue(r.ok, msg=r.message)
        assert r.patch is not None
        return r.patch.patch_id

    def test_M7_A_merge_without_confirm_cannot_approve(self) -> None:
        bid = self._open()
        pid = self._enqueue(bid, "合入后文本补充错误处理。")
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        # 伪造「跳过确认轮」：关掉 active 但仍记 had_write
        st = self.svc.confirm.get(self.rid)
        st.active = False
        with self.assertRaises(ValueError) as ctx:
            self.svc.judge_command(
                self.rid, "JudgeApprove", agent_id=self.judge.agent_id
            )
        self.assertIn("确认轮", str(ctx.exception))

    def test_M7_B_silent_path_one_approve(self) -> None:
        self._open("静默通过正文")
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ReviewOpen")
        self.assertFalse(self.svc.confirm.get(self.rid).had_write)
        self.svc.judge_command(
            self.rid, "JudgeApprove", agent_id=self.judge.agent_id
        )
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "Final")
        self.assertTrue(room.gate_passed)
        self.svc.commit_final_reply(self.rid)
        self.assertTrue(room.final_reply)

    def test_M7_C_confirm_rejects_outside_changed_set(self) -> None:
        self.svc.ask_question(self.rid, "题")
        self._roles()
        self.svc.submit_first_answer(
            self.rid,
            self.worker.agent_id,
            "# A\n\n第一块内容。\n\n# B\n\n第二块内容。\n",
        )
        doc = self.svc.docs.require_active(self.rid)
        ids = [b.block_id for b in doc.active_blocks()]
        self.assertIn("B01", ids)
        self.assertIn("B02", ids)
        pid = self._enqueue("B01", "第一块已修：补充错误处理。")
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        self.assertEqual(self.svc.confirm.changed_set(self.rid), {"B01"})
        # 确认轮打非 ChangedSet
        r = self.svc.ingest_worker_output(
            self.rid,
            self.worker.agent_id,
            json.dumps(
                {
                    "type": "Patch",
                    "target": "B02",
                    "category": "logic",
                    "claim": "缺错误处理会导致崩溃",
                    "replace": "第二块非法修改。",
                }
            ),
        )
        self.assertFalse(r.entered_queue)
        self.assertIn("ChangedSet", r.error)

    def test_M7_D_clean_without_approve_no_final(self) -> None:
        bid = self._open()
        pid = self._enqueue(bid, "合入文本含错误处理。")
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        self.svc._make_confirm_ready(self.rid)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ConfirmOpen")
        self.assertFalse(room.gate_passed)
        self.assertIsNone(room.final_reply)
        with self.assertRaises(ValueError):
            self.svc.commit_final_reply(self.rid, "不应写入")

    def test_M7_E_confirm_cap_escalates(self) -> None:
        bid = self._open()
        bud = self.svc.orch.require(self.rid).budget
        bud.config.max_confirm_churn = 1
        self.svc.confirm.get(self.rid).max_confirm_churn = 1
        pid1 = self._enqueue(bid, "第一次合入文本足够实质。")
        self.svc.judge_command(self.rid, "Accept", patch_id=pid1)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ConfirmOpen")
        # 确认轮内再合入 → 第二轮，封顶
        pid2 = self._enqueue(bid, "第二次合入再改一版错误处理。")
        self.svc.judge_command(self.rid, "Accept", patch_id=pid2)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "AwaitingUserEscalation")
        choices = self.svc.orch.user_escalation_choices(self.rid)
        self.assertEqual(len(choices), 4)
        self.svc.apply_user_escalation(self.rid, "end_without_final")
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "Idle")
        self.assertFalse(room.gate_passed)

    def test_M7_F_r2_clears_confirm(self) -> None:
        bid = self._open()
        pid = self._enqueue(bid, "合入后将被 R2 清零。")
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        self.assertTrue(self.svc.confirm.get(self.rid).had_write)
        self.assertTrue(self.svc.confirm.get(self.rid).active)
        self.svc.judge_command(
            self.rid,
            "R2",
            user_goal_ref="用户任务原话",
            wrong_direction="方向偏离",
            required_direction="按原话重做",
            keep=["无"],
            discard=["旧"],
        )
        st = self.svc.confirm.get(self.rid)
        self.assertFalse(st.had_write)
        self.assertFalse(st.active)
        self.assertEqual(st.changed_set, set())


if __name__ == "__main__":
    unittest.main()
