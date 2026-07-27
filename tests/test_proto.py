"""T-PROTO 验收：归一化闸门、闲聊隔离、缺 claim 拒收、重试。"""

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
from multi_agent_room.event_bus import EventBus  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.protocol import ProtocolNormalizer  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402


class ProtoTests(unittest.TestCase):
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
        self.worker = self.agents.create_agent(
            display_name="W",
            model_config_id=self.cfg.config_id,
            agent_id="agent-w",
        )
        self.judge = self.agents.create_agent(
            display_name="J",
            model_config_id=self.cfg.config_id,
            agent_id="agent-j",
        )
        self.room = self.svc.create_room("PROTO房")
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.svc.invite_ready_agent(self.room.room_id, self.judge.agent_id)
        self.rid = self.room.room_id

    def test_PROTO_01_worker_kinds(self) -> None:
        self.svc.ask_question(self.rid, "题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(
            self.rid, self.worker.agent_id, "open(path).read()\n"
        )
        r = self.svc.ingest_worker_output(
            self.rid,
            self.worker.agent_id,
            json.dumps(
                {"type": "Read", "agent_id": self.worker.agent_id, "version": 1}
            ),
        )
        self.assertTrue(r.ok)
        self.assertEqual(r.message.kind, "Read")
        self.assertTrue(any(e.type == "Read" for e in self.bus.history(self.rid)))

        r2 = self.svc.ingest_worker_output(
            self.rid,
            self.worker.agent_id,
            json.dumps(
                {
                    "type": "Patch",
                    "target": "B01",
                    "category": "fact",
                    "claim": "缺错误处理会导致崩溃",
                    "replace": "try:\n    open(path).read()\nexcept OSError:\n    pass\n",
                }
            ),
        )
        self.assertTrue(r2.ok)
        self.assertTrue(r2.entered_queue, msg=r2.error)
        self.assertEqual(len(self.svc.pending_patches(self.rid)), 1)

        r3 = self.svc.ingest_worker_output(
            self.rid,
            self.worker.agent_id,
            json.dumps(
                {
                    "type": "SilentCheckPass",
                    "agent_id": self.worker.agent_id,
                    "version": 1,
                    "doc_id": "d1",
                }
            ),
        )
        self.assertTrue(r3.ok)
        self.assertEqual(r3.message.kind, "SilentCheckPass")

    def test_PROTO_02_judge_kinds(self) -> None:
        self.svc.ask_question(self.rid, "题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, "稿")
        r = self.svc.ingest_judge_output(
            self.rid,
            self.judge.agent_id,
            json.dumps(
                {
                    "type": "R1",
                    "agent_id": self.judge.agent_id,
                    "target": "B01",
                    "reason": "局部缺漏",
                }
            ),
        )
        self.assertTrue(r.ok, msg=r.error)
        self.assertEqual(r.message.kind, "R1")
        self.assertTrue(
            any(
                e.type == "Verdict" and e.payload.get("verdict") == "R1"
                for e in self.bus.history(self.rid)
            )
        )

    def test_PROTO_03_chatter_private_not_queue(self) -> None:
        before = len(self.svc.pending_patches(self.rid))
        bus_before = len(self.bus.history(self.rid))
        r = self.svc.ingest_worker_output(
            self.rid, self.worker.agent_id, "你好啊，今天怎么样，随便聊聊"
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.route, "private")
        self.assertEqual(len(self.svc.pending_patches(self.rid)), before)
        new_types = [e.type for e in self.bus.history(self.rid)[bus_before:]]
        self.assertEqual(new_types, [])
        thoughts = self.agents.thoughts.read(self.worker.agent_id)
        self.assertTrue(any("闲聊" in t["text"] or "你好" in t["text"] for t in thoughts))

    def test_PROTO_03_missing_claim_not_public(self) -> None:
        r = self.svc.ingest_worker_output(
            self.rid,
            self.worker.agent_id,
            "PATCH target=B01 replace=改一下措辞优化一下",
        )
        self.assertFalse(r.ok)
        self.assertFalse(r.entered_queue)
        self.assertEqual(len(self.svc.pending_patches(self.rid)), 0)
        r2 = self.svc.ingest_worker_output(
            self.rid,
            self.worker.agent_id,
            json.dumps(
                {
                    "type": "Patch",
                    "target": "B01",
                    "category": "fact",
                    "replace": "xxx",
                }
            ),
        )
        self.assertFalse(r2.ok)
        self.assertIn("claim", r2.error)
        self.assertEqual(len(self.svc.pending_patches(self.rid)), 0)

    def test_PROTO_04_retry_limit(self) -> None:
        proto = ProtocolNormalizer(max_structure_retries=2)
        bad = "PATCH target=B01 replace=fix without claim field"
        r1 = proto.normalize_worker(room_id="r", agent_id="a", text=bad)
        self.assertEqual(r1.route, "retry", msg=r1.error)
        self.assertEqual(r1.retries_left, 1)
        self.assertIn("结构重发", r1.retry_hint)
        r2 = proto.normalize_worker(room_id="r", agent_id="a", text=bad)
        self.assertEqual(r2.route, "retry", msg=r2.error)
        self.assertEqual(r2.retries_left, 0)
        self.assertIn("结构重发", r2.retry_hint)
        r3 = proto.normalize_worker(room_id="r", agent_id="a", text=bad)
        self.assertIn(r3.route, ("private", "reject"))
        self.assertEqual(r3.retries_left, 0)
        self.assertIn("用尽", r3.retry_hint)

    def test_PROTO_05_field_gate_before_m5(self) -> None:
        self.svc.ask_question(self.rid, "题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(
            self.rid,
            self.worker.agent_id,
            "# A\n\n第一段推理内容。\n\n# B\n\n第二段待修正内容。\n",
        )
        doc = self.svc.docs.store.get_active(self.rid)
        assert doc is not None
        ids = [b.block_id for b in doc.active_blocks()]
        self.assertIn("B02", ids)
        ok = self.svc.ingest_worker_output(
            self.rid,
            self.worker.agent_id,
            "Patch\ntarget: B02\ncategory: logic\nclaim: 推理矛盾会导致错误结论\nreplace: 修正后的第二段内容，补充前提与推导步骤。",
        )
        self.assertTrue(ok.ok)
        self.assertTrue(ok.entered_queue, msg=ok.error)
        q = self.svc.pending_patches(self.rid)
        self.assertEqual(q[0].fields["target"], "B02")
        self.assertEqual(q[0].fields["claim"], "推理矛盾会导致错误结论")


if __name__ == "__main__":
    unittest.main()
