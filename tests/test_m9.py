"""T-M9 验收：M9-A～M9-C。"""

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


class M9Tests(unittest.TestCase):
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
        self.cfg_a = self.models.add_model(
            display_name="A",
            base_url="https://a.example/v1",
            model_id="a",
            api_key="sk-a",
        )
        self.cfg_b = self.models.add_model(
            display_name="B",
            base_url="https://b.example/v1",
            model_id="b",
            api_key="sk-b",
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "ok"),
        ):
            self.models.probe(self.cfg_a.config_id)
            self.models.probe(self.cfg_b.config_id)
        self.agent_a = self.agents.create_agent(
            display_name="A",
            model_config_id=self.cfg_a.config_id,
            agent_id="agent-a",
        )
        self.agent_b = self.agents.create_agent(
            display_name="B",
            model_config_id=self.cfg_b.config_id,
            agent_id="agent-b",
        )
        self.room = self.svc.create_room("M9房")
        self.svc.invite_ready_agent(self.room.room_id, self.agent_a.agent_id)
        self.svc.invite_ready_agent(self.room.room_id, self.agent_b.agent_id)
        self.rid = self.room.room_id

    def _silent_to_final(self, final_text: str = "终稿指针正文可检索") -> None:
        self.svc.ask_question(self.rid, "记忆验收题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.agent_a.agent_id,
            judge_agent_id=self.agent_b.agent_id,
            reviewer_agent_ids=[self.agent_a.agent_id],
        )
        self.svc.submit_first_answer(self.rid, self.agent_a.agent_id, final_text)
        self.svc.judge_command(
            self.rid, "JudgeApprove", agent_id=self.agent_b.agent_id
        )
        self.svc.commit_final_reply(self.rid)

    def test_M9_A_final_pointer_searchable(self) -> None:
        marker = "终稿指针正文可检索-UNIQUE-M9A"
        self._silent_to_final(marker)
        hits = self.svc.search_shared_memory(
            self.rid, query="UNIQUE-M9A", kind="final_pointer"
        )
        self.assertTrue(hits, msg="终稿指针应可检索")
        self.assertEqual(hits[0].kind, "final_pointer")
        self.assertIn(marker, hits[0].content)
        self.assertTrue(hits[0].resolved)
        # 过程态：提问时写入的 anchor 不得已 resolved
        anchors = self.svc.search_shared_memory(self.rid, kind="anchor_summary")
        self.assertTrue(anchors)
        self.assertFalse(anchors[0].resolved)

    def test_M9_B_private_draft_not_in_peer_context(self) -> None:
        self.svc.ask_question(self.rid, "私有隔离题")
        secret = "A私有草稿SECRET-M9B-勿泄露"
        self.svc.write_private_memory(
            self.rid, self.agent_a.agent_id, secret, tag="draft"
        )
        # B 搜 A 的私有 → 空
        cross = self.svc.search_private_memory(
            self.agent_b.agent_id,
            room_id=self.rid,
            target_agent_id=self.agent_a.agent_id,
            query="SECRET-M9B",
        )
        self.assertEqual(cross, [])
        # B 的组装上下文不含 A 草稿
        ctx_b = self.svc.agent_memory_context(self.agent_b.agent_id, self.rid)
        blob = str(ctx_b)
        self.assertNotIn(secret, blob)
        peer = ctx_b.get("peer_private_memory") or {}
        self.assertEqual(peer.get(self.agent_a.agent_id), [])
        # A 自己可见
        own = self.svc.search_private_memory(
            self.agent_a.agent_id, room_id=self.rid, query="SECRET-M9B"
        )
        self.assertEqual(len(own), 1)
        self.svc.memory.assert_private_no_leak(
            self.agent_b.agent_id,
            [self.agent_a.agent_id, self.agent_b.agent_id],
            room_id=self.rid,
        )

    def test_M9_C_memory_write_blocks_rejected(self) -> None:
        self.svc.ask_question(self.rid, "旁路拒收题")
        with self.assertRaises(PermissionError) as ctx:
            self.svc.write_shared_memory(
                self.rid,
                "todo",
                "看似普通记忆",
                meta={"blocks": [{"block_id": "b1", "text": "偷改"}]},
            )
        self.assertIn("旁路", str(ctx.exception))
        with self.assertRaises(PermissionError):
            self.svc.write_private_memory(
                self.rid,
                self.agent_a.agent_id,
                "draft",
                blocks=[{"block_id": "b1", "text": "x"}],
            )
        with self.assertRaises(PermissionError):
            self.svc.memory.write_shared(
                self.rid,
                "constraint",
                "nested",
                meta={"inner": {"blocks[]": "bad"}},
            )


if __name__ == "__main__":
    unittest.main()
