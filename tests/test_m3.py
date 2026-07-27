"""T-M3 验收：M3-A～M3-I。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_room.agent_service import AgentService  # noqa: E402
from multi_agent_room.adapters import ProbeResult  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402


class M3Tests(unittest.TestCase):
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
        self.svc = AgentService(models=self.models)
        self.room = "room-m3"

        # 两个不同模型配置
        self.cfg_a = self.models.add_model(
            display_name="ModelA",
            base_url="https://a.example/v1",
            model_id="a",
            api_key="sk-a",
        )
        self.cfg_b = self.models.add_model(
            display_name="ModelB",
            base_url="https://b.example/v1",
            model_id="b",
            api_key="sk-b",
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "探活成功"),
        ):
            self.models.probe(self.cfg_a.config_id)
            self.models.probe(self.cfg_b.config_id)

        self.agent_w = self.svc.create_agent(
            display_name="Worker",
            model_config_id=self.cfg_a.config_id,
            agent_id="agent-worker",
        )
        self.agent_j = self.svc.create_agent(
            display_name="Judge",
            model_config_id=self.cfg_b.config_id,
            agent_id="agent-judge",
        )

    def test_M3_A_same_agent_across_rooms(self) -> None:
        self.svc.invite_to_room("room-1", self.agent_w.agent_id)
        self.svc.invite_to_room("room-2", self.agent_w.agent_id)
        self.assertEqual(
            self.svc.roles.get_or_create("room-1").invited_agent_ids[0],
            self.agent_w.agent_id,
        )
        self.assertEqual(
            self.svc.roles.get_or_create("room-2").invited_agent_ids[0],
            self.agent_w.agent_id,
        )

    def test_M3_B_session_isolation(self) -> None:
        self.svc.invite_to_room(self.room, self.agent_w.agent_id)
        self.svc.invite_to_room(self.room, self.agent_j.agent_id)
        self.svc.sessions.assert_isolated(
            self.room, self.agent_w.agent_id, self.agent_j.agent_id
        )

    def test_M3_C_first_answerer_cannot_be_judge(self) -> None:
        self.svc.invite_to_room(self.room, self.agent_w.agent_id)
        self.svc.invite_to_room(self.room, self.agent_j.agent_id)
        self.svc.user_ask(self.room, "测试题")
        ok, _ = self.svc.claim_role(self.room, self.agent_w.agent_id, "first_answerer")
        self.assertTrue(ok)
        ok2, reason = self.svc.claim_role(self.room, self.agent_w.agent_id, "judge")
        self.assertFalse(ok2)
        self.assertIn("资格锁", reason)

    def test_M3_D_degrade_a_single_model(self) -> None:
        room = "room-single"
        # 仅邀请一个 agent
        self.svc.invite_to_room(room, self.agent_w.agent_id)
        self.svc.user_ask(room, "单模型题")
        self.svc.claim_role(room, self.agent_w.agent_id, "first_answerer")
        self.svc.roles.set_ready_model_count(room, 1)
        ok, _ = self.svc.roles.freeze_campaign(room)
        self.assertTrue(ok)
        st = self.svc.roles.get_or_create(room)
        self.assertTrue(st.roles.judge_is_user)
        can, reason = self.svc.roles.can_judge_approve(room, self.agent_w.agent_id)
        self.assertFalse(can)
        self.assertIn("降级 A", reason)
        can_user, _ = self.svc.roles.can_judge_approve(room, None)
        self.assertTrue(can_user)

    def test_M3_E_forged_message_rejected(self) -> None:
        self.svc.invite_to_room(self.room, self.agent_w.agent_id)
        self.svc.invite_to_room(self.room, self.agent_j.agent_id)
        self.svc.user_ask(self.room, "防伪")
        ok, reason = self.svc.forge_and_submit(
            pretend_agent_id=self.agent_j.agent_id,
            real_signer_agent_id=self.agent_w.agent_id,
            room_id=self.room,
            msg_type="Patch",
            payload={"target": "B1", "claim": "x"},
        )
        self.assertFalse(ok)
        self.assertTrue("伪造" in reason or "签名" in reason or "密钥" in reason)

    def test_M3_F_worker_patch_ok_judge_approve_fail(self) -> None:
        self.svc.invite_to_room(self.room, self.agent_w.agent_id)
        self.svc.invite_to_room(self.room, self.agent_j.agent_id)
        self.svc.user_ask(self.room, "分工")
        self.svc.claim_role(self.room, self.agent_w.agent_id, "first_answerer")
        self.svc.claim_role(self.room, self.agent_j.agent_id, "judge")
        self.svc.freeze_roles(self.room)

        patch_msg = self.svc.sign_message(
            agent_id=self.agent_w.agent_id,
            room_id=self.room,
            msg_type="Patch",
            payload={"target": "B1", "claim": "fix"},
        )
        ok_p, _ = self.svc.accept_message(patch_msg)
        self.assertTrue(ok_p)

        bad = self.svc.sign_message(
            agent_id=self.agent_w.agent_id,
            room_id=self.room,
            msg_type="JudgeApprove",
            payload={},
        )
        ok_j, reason = self.svc.accept_message(bad)
        self.assertFalse(ok_j)
        self.assertIn("JudgeApprove", reason)

        good = self.svc.sign_message(
            agent_id=self.agent_j.agent_id,
            room_id=self.room,
            msg_type="JudgeApprove",
            payload={},
        )
        ok_ok, _ = self.svc.accept_message(good)
        self.assertTrue(ok_ok)

    def test_M3_G_user_assign_overrides(self) -> None:
        self.svc.invite_to_room(self.room, self.agent_w.agent_id)
        self.svc.invite_to_room(self.room, self.agent_j.agent_id)
        self.svc.user_ask(self.room, "用户指定")
        # 竞选中工人想当评判 — 先不认领首答
        self.svc.claim_role(self.room, self.agent_w.agent_id, "reviewer")
        ok, _ = self.svc.user_assign_roles(
            self.room,
            first_answerer_agent_id=self.agent_w.agent_id,
            judge_agent_id=self.agent_j.agent_id,
        )
        self.assertTrue(ok)
        st = self.svc.roles.get_or_create(self.room)
        self.assertTrue(st.roles.frozen)
        self.assertEqual(st.roles.first_answerer_agent_id, self.agent_w.agent_id)
        self.assertEqual(st.roles.judge_agent_id, self.agent_j.agent_id)
        # 冻结后不可自行改职
        ok2, reason = self.svc.claim_role(self.room, self.agent_w.agent_id, "judge")
        self.assertFalse(ok2)
        self.assertIn("冻结", reason)

    def test_M3_H_private_thought_no_leak(self) -> None:
        self.svc.invite_to_room(self.room, self.agent_w.agent_id)
        self.svc.invite_to_room(self.room, self.agent_j.agent_id)
        secret = "工人私有草稿-不可泄露-XYZ"
        self.svc.think(self.agent_w.agent_id, secret)
        ctx = self.svc.context_for(self.agent_j.agent_id, self.room)
        self.assertEqual(ctx["peer_thoughts"].get(self.agent_w.agent_id), [])
        self.assertNotIn(secret, str(ctx))
        self.svc.thoughts.assert_no_leak(
            self.agent_j.agent_id, [self.agent_w.agent_id, self.agent_j.agent_id]
        )

    def test_M3_I_idle_without_question(self) -> None:
        self.svc.invite_to_room(self.room, self.agent_w.agent_id)
        ok, reason = self.svc.roles.start_campaign_without_question(self.room)
        self.assertFalse(ok)
        self.assertIn("未提问", reason)
        ok2, reason2 = self.svc.claim_role(self.room, self.agent_w.agent_id, "first_answerer")
        self.assertFalse(ok2)
        self.assertIn("未提问", reason2)
        # 未冻结不得写共享稿
        ok3, reason3 = self.svc.roles.can_write_shared_doc(self.room)
        self.assertFalse(ok3)


if __name__ == "__main__":
    unittest.main()
