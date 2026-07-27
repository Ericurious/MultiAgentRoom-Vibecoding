"""T-M2 验收：M2-A～M2-G。"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_room.adapters import ProbeResult  # noqa: E402
from multi_agent_room.agent_service import AgentService  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402


class M2Tests(unittest.TestCase):
    def setUp(self) -> None:
        setup_logging()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._env = patch.dict("os.environ", {"APPDATA": self._tmpdir.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        import multi_agent_room.secret_store as ss

        ss._STORE = None
        self.models = ModelService()
        self.agents = AgentService(models=self.models)
        self.svc = RoomService(agents=self.agents, models=self.models)

        self.cfg = self.models.add_model(
            display_name="ModelA",
            base_url="https://a.example/v1",
            model_id="a",
            api_key="sk-a",
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "探活成功"),
        ):
            self.models.probe(self.cfg.config_id)

        self.agent = self.agents.create_agent(
            display_name="Worker",
            model_config_id=self.cfg.config_id,
            agent_id="agent-ready",
        )

    def _room_with_member(self):
        room = self.svc.create_room("测试房")
        self.svc.invite_ready_agent(room.room_id, self.agent.agent_id)
        return self.svc.get_room(room.room_id)

    def test_M2_A_create_invite_ready(self) -> None:
        room = self._room_with_member()
        assert room is not None
        self.assertGreaterEqual(len(room.invited_agent_ids), 1)
        self.assertIn(self.agent.agent_id, room.invited_agent_ids)
        # 未就绪不可邀
        cfg_b = self.models.add_model(
            display_name="Fail",
            base_url="https://b.example/v1",
            model_id="b",
            api_key="sk-b",
        )
        bad = self.agents.create_agent(
            display_name="NotReady",
            model_config_id=cfg_b.config_id,
            agent_id="agent-bad",
        )
        with self.assertRaises(ValueError):
            self.svc.invite_ready_agent(room.room_id, bad.agent_id)

    def test_M2_B_pin_question(self) -> None:
        room = self._room_with_member()
        assert room is not None
        q = "请实现登录页并遵守原话约束"
        self.svc.ask_question(room.room_id, q)
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertEqual(room.pinned_question, q)

    def test_M2_C_agenda_to_review_open(self) -> None:
        room = self._room_with_member()
        assert room is not None
        self.svc.ask_question(room.room_id, "议程演示")
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertEqual(room.phase, "Campaign")
        self.agents.user_assign_roles(
            room.room_id,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(room.room_id, self.agent.agent_id, "首答正文")
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertEqual(room.phase, "ReviewOpen")
        self.assertIn("ReviewOpen", room.agenda_text())
        doc = self.svc.get_doc(room.room_id)
        self.assertEqual(doc.blocks[0].block_id, "B01")
        self.assertGreaterEqual(doc.doc_version, 1)

    def test_M2_D_qualification_lock_display(self) -> None:
        room = self._room_with_member()
        assert room is not None
        self.svc.ask_question(room.room_id, "资格锁")
        self.agents.user_assign_roles(
            room.room_id,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(room.room_id, self.agent.agent_id, "ans")
        room = self.svc.get_room(room.room_id)
        assert room is not None
        text = room.qualification_lock_text()
        self.assertIn(self.cfg.config_id, text)
        self.assertIn("不可评判", text)

    def test_M2_E_interrupt_keeps_review_open(self) -> None:
        room = self._room_with_member()
        assert room is not None
        self.svc.ask_question(room.room_id, "打断")
        self.agents.user_assign_roles(
            room.room_id,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(room.room_id, self.agent.agent_id, "ans")
        # 人为把截止时间推到过去
        room = self.svc.get_room(room.room_id)
        assert room is not None
        room.review_deadline_ts = time.time() - 1
        self.svc.store.upsert(room)

        self.svc.interrupt(room.room_id)
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertTrue(room.frozen)
        self.assertTrue(room.review_window_open)
        ok, reason = self.svc.try_silence_pass(room.room_id)
        self.assertFalse(ok)
        self.assertIn("Frozen", reason)

        self.svc.resume(room.room_id)
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertFalse(room.frozen)
        self.assertEqual(room.phase, "ReviewOpen")
        self.assertTrue(room.review_window_open)

    def test_M2_F_final_slot_before_gate(self) -> None:
        room = self._room_with_member()
        assert room is not None
        self.svc.ask_question(room.room_id, "终稿")
        self.assertEqual(self.svc.final_slot_text(room.room_id), "未通过")
        with self.assertRaises(ValueError):
            self.svc.commit_final(room.room_id, "不该出现的终稿")
        self.agents.user_assign_roles(
            room.room_id,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(room.room_id, self.agent.agent_id, "ans")
        self.assertEqual(self.svc.final_slot_text(room.room_id), "未通过")
        self.svc.judge_command(room.room_id, "JudgeApprove")
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertEqual(room.phase, "Final")
        self.svc.commit_final(room.room_id, "门禁后终稿")
        self.assertEqual(self.svc.final_slot_text(room.room_id), "门禁后终稿")

    def test_M2_G_clarify_blocks_silence_pass(self) -> None:
        room = self._room_with_member()
        assert room is not None
        self.svc.ask_question(room.room_id, "澄清")
        self.agents.user_assign_roles(
            room.room_id,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(room.room_id, self.agent.agent_id, "ans")
        room = self.svc.get_room(room.room_id)
        assert room is not None
        room.review_deadline_ts = time.time() - 10
        self.svc.store.upsert(room)

        self.svc.ask_clarify(room.room_id, "端口用多少？", from_agent=self.agent.agent_id)
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertEqual(room.phase, "AwaitingUserClarify")
        self.assertTrue(room.review_window_open)
        ok, reason = self.svc.try_silence_pass(room.room_id)
        self.assertFalse(ok)
        self.assertIn("澄清", reason)

        self.svc.answer_clarify(room.room_id, "8080")
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertEqual(room.phase, "ReviewOpen")

    def test_workspace_boundary(self) -> None:
        room = self._room_with_member()
        assert room is not None
        ws = Path(self._tmpdir.name) / "ws"
        self.svc.set_workspace(room.room_id, ws)
        inside = self.svc.assert_path_in_workspace(room.room_id, ws / "ok.txt")
        self.assertTrue(str(inside).startswith(str(ws.resolve())))
        with self.assertRaises(PermissionError):
            self.svc.assert_path_in_workspace(
                room.room_id, Path(self._tmpdir.name) / "outside" / "x.txt"
            )


if __name__ == "__main__":
    unittest.main()
