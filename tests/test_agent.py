"""T-AGENT 验收：空闲无 W1、静默通过路径、工人/评议分工、私有思考隔离。"""

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
from multi_agent_room.prompts import build_j1_prompt, build_w4_prompt  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402
from multi_agent_room.worker_runtime import AgentRuntime  # noqa: E402


class AgentTests(unittest.TestCase):
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
        self.rt = AgentRuntime(self.svc)

        self.cfg_w = self.models.add_model(
            display_name="WorkerModel",
            base_url="https://w.example/v1",
            model_id="w",
            api_key="sk-w",
        )
        self.cfg_j = self.models.add_model(
            display_name="JudgeModel",
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
            agent_id="agent-worker",
        )
        self.judge = self.agents.create_agent(
            display_name="Judge",
            model_config_id=self.cfg_j.config_id,
            agent_id="agent-judge",
        )
        self.room = self.svc.create_room("AGENT房")
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.svc.invite_ready_agent(self.room.room_id, self.judge.agent_id)
        self.rid = self.room.room_id

    def test_E2E15_idle_no_w1(self) -> None:
        calls = []

        def caller(msgs, aid):
            calls.append(aid)
            return "should-not-run"

        self.rt.caller = caller
        r = self.rt.run_w1(self.rid)
        self.assertFalse(r.ok)
        self.assertIn("未提问", r.error)
        self.assertFalse(r.model_called)
        self.assertEqual(calls, [])
        self.assertEqual(self.rt.call_log, [])

    def test_E2E09_silent_pass_to_final(self) -> None:
        self.svc.ask_question(self.rid, "写一个登录页")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_agent_id=self.judge.agent_id,
        )
        w1 = self.rt.run_w1(
            self.rid, self.worker.agent_id, response="【首答】登录页方案正文"
        )
        self.assertTrue(w1.ok)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ReviewOpen")

        w2 = self.rt.run_w2(
            self.rid,
            self.worker.agent_id,
            response=json.dumps(
                {
                    "type": "SilentCheckPass",
                    "agent_id": self.worker.agent_id,
                    "version": 1,
                    "doc_id": "d1",
                }
            ),
        )
        self.assertTrue(w2.ok)
        self.assertEqual(w2.proto.message.kind, "SilentCheckPass")
        self.assertTrue(
            any(e.type == "SilentCheckPass" for e in self.bus.history(self.rid))
        )

        j1 = self.rt.run_j1(
            self.rid,
            self.judge.agent_id,
            response=json.dumps(
                {"type": "JudgeApprove", "agent_id": self.judge.agent_id}
            ),
        )
        self.assertTrue(j1.ok, msg=j1.error)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "Final")
        self.assertTrue(room.gate_passed)

    def test_E2E12_worker_patch_not_approve(self) -> None:
        self.svc.ask_question(self.rid, "分工")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_agent_id=self.judge.agent_id,
        )
        self.rt.run_w1(self.rid, self.worker.agent_id, response="首答正文")
        # 工人可 PATCH
        patch = self.rt.run_w2(
            self.rid,
            self.worker.agent_id,
            response=json.dumps(
                {
                    "type": "Patch",
                    "target": "B01",
                    "category": "fact",
                    "claim": "缺校验",
                    "replace": "加校验",
                }
            ),
        )
        self.assertTrue(patch.ok)
        self.assertTrue(patch.proto.entered_queue)
        # 工人 JudgeApprove 失败
        bad = self.svc.ingest_judge_output(
            self.rid,
            self.worker.agent_id,
            json.dumps(
                {"type": "JudgeApprove", "agent_id": self.worker.agent_id}
            ),
        )
        self.assertFalse(bad.ok)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertNotEqual(room.phase, "Final")
        # 合入 → 确认轮干净后评议才可 Approve（M7）
        pid = self.svc.pending_patches(self.rid)[0].patch_id
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        self.svc._make_confirm_ready(self.rid)
        ok = self.rt.run_j1(
            self.rid,
            self.judge.agent_id,
            response=json.dumps(
                {"type": "JudgeApprove", "agent_id": self.judge.agent_id}
            ),
        )
        self.assertTrue(ok.ok, msg=ok.error)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "Final")

    def test_AGENT_08_private_thought_not_in_j1(self) -> None:
        secret = "工人私有草稿-SECRET-XYZ"
        self.agents.think(self.worker.agent_id, secret)
        self.svc.ask_question(self.rid, "隔离")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_agent_id=self.judge.agent_id,
        )
        self.rt.run_w1(self.rid, response="公开首答")
        msgs = build_j1_prompt(
            question="隔离",
            current_doc="公开首答",
            pending_patches=[],
        )
        blob = "\n".join(m["content"] for m in msgs)
        self.assertNotIn(secret, blob)
        self.agents.thoughts.assert_no_leak(
            self.judge.agent_id, [self.worker.agent_id, self.judge.agent_id]
        )
        # run_j1 也会校验
        j = self.rt.run_j1(
            self.rid,
            self.judge.agent_id,
            response=json.dumps(
                {
                    "type": "R1",
                    "agent_id": self.judge.agent_id,
                    "target": "B01",
                    "reason": "局部缺漏",
                }
            ),
        )
        self.assertTrue(j.ok, msg=j.error)
        self.assertNotIn(secret, "\n".join(m["content"] for m in j.prompt))

    def test_AGENT_06_w4_changed_set_in_prompt(self) -> None:
        self.svc.ask_question(self.rid, "确认轮")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
        )
        self.rt.run_w1(self.rid, response="稿")
        r = self.rt.run_w4(
            self.rid,
            changed_set=["B01", "B03"],
            response=json.dumps(
                {"type": "Read", "agent_id": self.worker.agent_id, "version": 1}
            ),
        )
        self.assertTrue(r.ok)
        blob = "\n".join(m["content"] for m in r.prompt)
        self.assertIn("ChangedSet", blob)
        self.assertIn("B01", blob)
        self.assertIn("B03", blob)
        # 无 ChangedSet 应失败
        bad = self.rt.run_w4(self.rid, changed_set=None, response="{}")
        self.assertFalse(bad.ok)

    def test_AGENT_09_single_worker_default(self) -> None:
        self.svc.ask_question(self.rid, "单工人")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
        )
        self.assertEqual(self.rt.primary_worker_id(self.rid), self.worker.agent_id)
        r = self.rt.run_w1(self.rid, response="only-one")
        self.assertEqual(r.agent_id, self.worker.agent_id)


if __name__ == "__main__":
    unittest.main()
