"""T-BUS 验收：发布/订阅、类型全集、持久化回放、ToolReceipt、Idle/Awake。"""

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
from multi_agent_room.event_types import BUS_EVENT_TYPES, MERGE_PAIR  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402


class BusTests(unittest.TestCase):
    def setUp(self) -> None:
        setup_logging()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._env = patch.dict("os.environ", {"APPDATA": self._tmpdir.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        import multi_agent_room.secret_store as ss

        ss._STORE = None
        self.bus = EventBus(persist=True)
        self.models = ModelService()
        self.agents = AgentService(models=self.models)
        self.svc = RoomService(
            agents=self.agents, models=self.models, bus=self.bus
        )
        self.cfg = self.models.add_model(
            display_name="A",
            base_url="https://a.example/v1",
            model_id="a",
            api_key="sk-a",
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "ok"),
        ):
            self.models.probe(self.cfg.config_id)
        self.agent = self.agents.create_agent(
            display_name="W",
            model_config_id=self.cfg.config_id,
            agent_id="agent-bus",
        )

    def _room(self):
        room = self.svc.create_room("总线房")
        self.svc.invite_ready_agent(room.room_id, self.agent.agent_id)
        return room

    def test_BUS_01_pubsub_room_isolated(self) -> None:
        room = self._room()
        other = self.svc.create_room("另一房")
        got: list[str] = []
        self.bus.subscribe(
            room.room_id, lambda e: got.append(e.type), types={"Read"}
        )
        self.svc.record_read(room.room_id, agent_id=self.agent.agent_id)
        self.svc.record_read(other.room_id, agent_id="x")
        self.assertEqual(got, ["Read"])

    def test_BUS_02_type_catalog(self) -> None:
        expected = {
            "DocVersion",
            "Read",
            "PatchAccepted",
            "PatchRejected",
            "QueueUpdated",
            "Verdict",
            "JudgeApprove",
            "FinalCommitted",
            "ToolReceipt",
            "Clarify",
            "SilentCheckPass",
            "RoomIdle",
            "RoomAwake",
        }
        self.assertEqual(BUS_EVENT_TYPES, expected)
        room = self._room()
        with self.assertRaises(ValueError):
            self.bus.publish(room.room_id, "PrivateDM", {})

    def test_BUS_03_persist_replay_order(self) -> None:
        room = self._room()
        self.bus.publish(room.room_id, "Read", {"n": 1})
        self.bus.publish(room.room_id, "SilentCheckPass", {"n": 2})
        self.bus.emit_after_merge(room.room_id, doc_version=1, patch_id="p1")
        mem = self.bus.replay(room.room_id)
        disk = self.bus.replay(room.room_id, from_disk=True)
        self.assertEqual([e.seq for e in mem], sorted(e.seq for e in mem))
        self.assertEqual([e.type for e in mem], [e.type for e in disk])
        # 含合入配对且顺序 DocVersion → QueueUpdated
        types = [e.type for e in mem]
        i_dv = types.index("DocVersion")
        i_qu = types.index("QueueUpdated")
        self.assertLess(i_dv, i_qu)
        self.assertEqual((types[i_dv], types[i_qu]), MERGE_PAIR)

    def test_BUS_04_tool_receipt(self) -> None:
        room = self._room()
        ev = self.svc.publish_tool_receipt(
            room.room_id,
            tool_name="write_file",
            ok=True,
            exit_code=0,
            paths=["a.txt"],
            diff_summary="+1",
            mcp_id="mcp-demo",
        )
        self.assertEqual(ev.type, "ToolReceipt")
        self.assertEqual(ev.payload["tool_name"], "write_file")
        self.assertTrue(any(e.type == "ToolReceipt" for e in self.bus.history(room.room_id)))

    def test_BUS_05_idle_awake(self) -> None:
        room = self._room()
        self.assertTrue(self.bus.is_idle(room.room_id))
        types = [e.type for e in self.bus.history(room.room_id)]
        self.assertIn("RoomIdle", types)
        self.svc.ask_question(room.room_id, "唤醒")
        self.assertFalse(self.bus.is_idle(room.room_id))
        types2 = [e.type for e in self.bus.history(room.room_id)]
        self.assertIn("RoomAwake", types2)

    def test_merge_emits_docversion_queueupdated(self) -> None:
        room = self._room()
        self.svc.ask_question(room.room_id, "合入")
        self.agents.user_assign_roles(
            room.room_id,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(room.room_id, self.agent.agent_id, "正文")
        self.svc.judge_command(room.room_id, "Merge", patch_id="p-merge")
        types = [e.type for e in self.bus.history(room.room_id)]
        # 至少一对 DocVersion+QueueUpdated（合入）
        pairs = [
            (types[i], types[i + 1])
            for i in range(len(types) - 1)
            if (types[i], types[i + 1]) == MERGE_PAIR
        ]
        self.assertTrue(pairs, msg=f"types={types}")

    def test_no_direct_message(self) -> None:
        with self.assertRaises(RuntimeError):
            self.bus.send_direct("a", "b", "hi")


if __name__ == "__main__":
    unittest.main()
