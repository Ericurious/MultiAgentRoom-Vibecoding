"""T-M8b 验收：M8b-A～B（M8b-C 为 P1a 最小实现可测）。"""

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


class M8bTests(unittest.TestCase):
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
        self.room = self.svc.create_room("M8b房")
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.rid = self.room.room_id

    def _open(self) -> str:
        self.svc.ask_question(self.rid, "审计题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, "原始稿正文。\n")
        return self.svc.docs.require_active(self.rid).active_blocks()[0].block_id

    def test_M8b_A_replay_accept_and_r1(self) -> None:
        bid = self._open()
        self.svc.judge_command(self.rid, "R1", target=bid, reason="缺边界")
        r = self.svc.review.submit_patch(
            room_id=self.rid,
            agent_id=self.worker.agent_id,
            fields={
                "target": bid,
                "category": "logic",
                "claim": "缺错误处理会导致崩溃",
                "replace": "重交后带边界检查的正文。",
            },
            old_text=self.svc.docs.require_active(self.rid).get_block(bid).text,
            doc_full=self.svc.docs.require_active(self.rid).full_text(),
            doc_version=1,
        )
        self.assertTrue(r.ok, msg=r.message)
        assert r.patch is not None
        self.svc.judge_command(self.rid, "Accept", patch_id=r.patch.patch_id)
        events = self.svc.replay_audit(self.rid, types=["Accept", "R1"])
        types = [e.type for e in events]
        self.assertIn("R1", types)
        self.assertIn("Accept", types)
        summary = "\n".join(self.svc.audit.summarize(self.rid))
        self.assertIn("R1", summary)
        self.assertIn("Accept", summary)

    def test_M8b_B_restore_version_and_queue(self) -> None:
        bid = self._open()
        r = self.svc.review.submit_patch(
            room_id=self.rid,
            agent_id=self.worker.agent_id,
            fields={
                "target": bid,
                "category": "logic",
                "claim": "缺错误处理会导致崩溃",
                "replace": "排队中的实质补丁正文。",
            },
            old_text=self.svc.docs.require_active(self.rid).get_block(bid).text,
            doc_full=self.svc.docs.require_active(self.rid).full_text(),
            doc_version=1,
        )
        self.assertTrue(r.ok, msg=r.message)
        assert r.patch is not None
        self.svc.judge_command(self.rid, "Accept", patch_id=r.patch.patch_id)
        # 确认轮再入队一条未决补丁
        r2 = self.svc.review.submit_patch(
            room_id=self.rid,
            agent_id=self.worker.agent_id,
            fields={
                "target": bid,
                "category": "logic",
                "claim": "缺错误处理会导致崩溃",
                "replace": "确认轮未决补丁。",
            },
            old_text=self.svc.docs.require_active(self.rid).get_block(bid).text,
            doc_full=self.svc.docs.require_active(self.rid).full_text(),
            doc_version=self.svc.docs.require_active(self.rid).version,
        )
        self.assertTrue(r2.ok, msg=r2.message)
        ver = self.svc.docs.require_active(self.rid).version
        pending_n = len(self.svc.pending_patches(self.rid))
        self.assertGreaterEqual(pending_n, 1)
        self.svc.persist_room(self.rid)

        # 模拟 Kill 进程：全新 RoomService 读同一 APPDATA
        bus2 = EventBus(persist=False)
        models2 = ModelService()
        agents2 = AgentService(models=models2)
        svc2 = RoomService(agents=agents2, models=models2, bus=bus2)
        # 模型/Agent 元数据需重载（profile 文件）；房间状态从 RoomState 恢复
        restored = svc2.restore_room(self.rid)
        self.assertEqual(restored.phase, "ConfirmOpen")
        doc = svc2.docs.store.get_active(self.rid)
        assert doc is not None
        self.assertEqual(doc.version, ver)
        self.assertEqual(len(svc2.pending_patches(self.rid)), pending_n)
        self.assertEqual(svc2.confirm.changed_set(self.rid), {bid})

    def test_M8b_C_archive_list_visible(self) -> None:
        """P1a 最小归档：归档后 index 可见。"""
        self._open()
        self.svc.persist_room(self.rid)
        self.svc.archive_room(self.rid)
        items = self.svc.list_archived_rooms()
        self.assertTrue(any(x.get("roomId") == self.rid for x in items))


if __name__ == "__main__":
    unittest.main()
