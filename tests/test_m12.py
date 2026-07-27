"""T-M12 验收：M12-A～M12-D。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_room.adapters import ProbeResult  # noqa: E402
from multi_agent_room.agent_service import AgentService  # noqa: E402
from multi_agent_room.event_bus import EventBus  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402


class M12Tests(unittest.TestCase):
    def setUp(self) -> None:
        setup_logging()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._env = patch.dict("os.environ", {"APPDATA": self._tmpdir.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        import multi_agent_room.secret_store as ss

        ss._STORE = None
        self.ws = Path(self._tmpdir.name) / "workspace"
        self.ws.mkdir(parents=True, exist_ok=True)

        self.bus = EventBus(persist=False)
        self.models = ModelService()
        self.agents = AgentService(models=self.models)
        self.svc = RoomService(
            agents=self.agents, models=self.models, bus=self.bus
        )
        self.cfg = self.models.add_model(
            display_name="W",
            base_url="https://w.example/v1",
            model_id="w",
            api_key="sk-w",
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "ok"),
        ):
            self.models.probe(self.cfg.config_id)
        self.worker = self.agents.create_agent(
            display_name="W",
            model_config_id=self.cfg.config_id,
            agent_id="agent-w",
        )
        self.room = self.svc.create_room("M12房", workspace_path=str(self.ws))
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.rid = self.room.room_id

    def _to_final(self, text: str = "正式终稿正文，非空。") -> None:
        self.svc.ask_question(self.rid, "交付验收题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, text)
        self.svc.judge_command(self.rid, "JudgeApprove")
        self.svc.commit_final_reply(self.rid)

    def test_M12_A_deliver_before_final_fails(self) -> None:
        self.svc.ask_question(self.rid, "未 Final 不可落盘")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, "审阅中稿")
        r = self.svc.click_deliver(self.rid)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "gate_denied")
        self.assertFalse((self.ws / "delivery" / "final-reply.md").exists())
        # JudgeApprove 但未 CommitFinalReply 仍非 FinalCommitted
        self.svc.judge_command(self.rid, "JudgeApprove")
        r2 = self.svc.click_deliver(self.rid)
        self.assertFalse(r2.ok)
        self.assertEqual(r2.code, "gate_denied")

    def test_M12_B_click_deliver_writes_nonempty_in_workspace(self) -> None:
        self._to_final("点交付后应落盘的正文。")
        r = self.svc.click_deliver(self.rid, summary="本轮总结要点。")
        self.assertTrue(r.ok, msg=r.message)
        main = self.ws / "delivery" / "final-reply.md"
        self.assertTrue(main.is_file())
        self.assertGreater(main.stat().st_size, 0)
        self.assertTrue(str(main).startswith(str(self.ws)))
        # 回执进房
        self.assertTrue(
            any(e.type == "ToolReceipt" for e in self.bus.history(self.rid))
        )
        tl = " ".join(
            f"{x.kind}" for x in self.svc.timeline.list_events(self.rid)
        )
        self.assertIn("DeliveryReceipt", tl)

    def test_M12_C_path_escape_fails(self) -> None:
        self._to_final()
        outside = str(Path(self._tmpdir.name) / "outside-escape.md")
        r = self.svc.click_deliver(self.rid, force_path=outside)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "path_escape")
        self.assertFalse(Path(outside).exists())

    def test_M12_D_manifest_matches_disk(self) -> None:
        self._to_final("清单一致性正文。")
        r = self.svc.click_deliver(
            self.rid,
            summary="总结",
            extra_files={"delivery/extra.txt": "extra-body"},
        )
        self.assertTrue(r.ok, msg=r.message)
        ok, msg = self.svc.verify_delivery_manifest(self.rid)
        self.assertTrue(ok, msg=msg)
        # 磁盘文件均存在且非空
        for item in r.items:
            p = Path(item.abs_path)
            self.assertTrue(p.is_file(), msg=item.rel_path)
            self.assertEqual(p.stat().st_size, item.size)

    def test_M12_authorize_token_bypass(self) -> None:
        """授权落盘：无 Final 但有 writeToken 可点交付。"""
        self.svc.ask_question(self.rid, "授权旁路")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, "未 Final 稿")
        self.svc.authorize_deliver(self.rid)
        r = self.svc.click_deliver(
            self.rid, content="经授权落盘的正文。", summary="授权总结"
        )
        self.assertTrue(r.ok, msg=r.message)
        self.assertEqual(r.gate, "writeToken")
        self.assertTrue((self.ws / "delivery" / "final-reply.md").is_file())


if __name__ == "__main__":
    unittest.main()
