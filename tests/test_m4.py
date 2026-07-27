"""T-M4 验收：M4-A～M4-E。"""

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
from multi_agent_room.chunker import chunk_text, looks_like_markdown  # noqa: E402
from multi_agent_room.event_bus import EventBus  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402
from multi_agent_room.shared_doc import DocService  # noqa: E402


class M4Tests(unittest.TestCase):
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
        self.agent = self.agents.create_agent(
            display_name="W",
            model_config_id=self.cfg.config_id,
            agent_id="agent-m4",
        )
        self.room = self.svc.create_room("M4房")
        self.svc.invite_ready_agent(self.room.room_id, self.agent.agent_id)
        self.rid = self.room.room_id

    def _ask_and_answer(self, text: str):
        self.svc.ask_question(self.rid, "题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(self.rid, self.agent.agent_id, text)
        return self.svc.docs.store.get_active(self.rid)

    def test_M4_A_first_answer_has_blocks(self) -> None:
        md = "# 标题\n\n第一段内容。\n\n## 小节\n\n第二段。\n"
        doc = self._ask_and_answer(md)
        assert doc is not None
        self.assertGreaterEqual(len(doc.active_blocks()), 1)
        ids = [b.block_id for b in doc.active_blocks()]
        self.assertTrue(all(i.startswith("B") for i in ids))
        self.assertEqual(doc.status, "active")
        self.assertEqual(doc.version, 1)

    def test_M4_B_missing_target_rejected(self) -> None:
        self._ask_and_answer("单段稿")
        ok, reason = self.svc.docs.can_patch_target(self.rid, None)
        self.assertFalse(ok)
        self.assertIn("target", reason)
        ok2, _ = self.svc.docs.can_patch_target(self.rid, "B99")
        self.assertFalse(ok2)

    def test_M4_C_parallel_candidates_one_active(self) -> None:
        docs = DocService()
        c1 = docs.add_candidate(self.rid, "# A\n\ncand1", agent_id="a1")
        c2 = docs.add_candidate(self.rid, "# B\n\ncand2", agent_id="a2")
        self.assertEqual(len(docs.list_candidates(self.rid)), 2)
        selected = docs.select_candidate(self.rid, c1.doc_id, by="user")
        self.assertEqual(selected.doc_id, c1.doc_id)
        self.assertEqual(selected.status, "active")
        active = docs.store.get_active(self.rid)
        assert active is not None
        self.assertEqual(active.doc_id, c1.doc_id)
        statuses = {c.doc.doc_id: c.doc.status for c in docs.list_candidates(self.rid)}
        self.assertEqual(statuses[c1.doc_id], "active")
        self.assertEqual(statuses[c2.doc_id], "rejected")

    def test_M4_D_merge_bumps_version_reads_not_inherit(self) -> None:
        doc = self._ask_and_answer("段落一\n\n段落二足够长一点。")
        assert doc is not None
        bid = doc.active_blocks()[0].block_id
        self.svc.record_read(self.rid, agent_id=self.agent.agent_id)
        reads_v1 = self.svc.docs.reads_valid_for_current(self.rid)
        self.assertEqual(len(reads_v1), 1)
        v_before = doc.version
        self.svc.judge_command(
            self.rid,
            "Accept",
            target=bid,
            replace="合入后的新文本",
            patch_id="p1",
        )
        doc2 = self.svc.docs.store.get_active(self.rid)
        assert doc2 is not None
        self.assertEqual(doc2.version, v_before + 1)
        # 旧已读不继承为新版同意
        self.assertEqual(len(self.svc.docs.reads_valid_for_current(self.rid)), 0)
        # blockId 稳定
        self.assertEqual(doc2.get_block(bid).text, "合入后的新文本")

    def test_M4_E_r2_voids_need_new_first(self) -> None:
        doc = self._ask_and_answer("将被 R2 作废的稿")
        assert doc is not None
        old_id = doc.doc_id
        self.svc.judge_command(
            self.rid,
            "R2",
            user_goal_ref="用户原问题",
            wrong_direction="整体方案偏离目标",
            required_direction="按原话重做首答",
            keep=["术语表"],
            discard=["旧结论"],
        )
        self.assertTrue(self.svc.docs.requires_new_first_answer(self.rid))
        self.assertIsNone(self.svc.docs.store.get_active(self.rid))
        # 历史中 voided
        hist = self.svc.docs.store._history.get(self.rid) or []
        self.assertTrue(any(d.doc_id == old_id and d.status == "voided" for d in hist))
        # 未新首答不可 require_active
        with self.assertRaises(ValueError):
            self.svc.docs.require_active(self.rid)
        # 新首答后可再审
        self.svc.submit_first_answer(self.rid, self.agent.agent_id, "R2 后新首答正文")
        new_doc = self.svc.docs.store.get_active(self.rid)
        assert new_doc is not None
        self.assertEqual(new_doc.status, "active")
        self.assertNotEqual(new_doc.doc_id, old_id)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ReviewOpen")

    def test_chunker_md_and_window(self) -> None:
        self.assertTrue(looks_like_markdown("# Hi\n\npara"))
        chunks = chunk_text("# T\n\n" + ("字" * 1200))
        self.assertGreaterEqual(len(chunks), 2)
        plain = chunk_text("第一段\n\n第二段")
        self.assertGreaterEqual(len(plain), 2)

    def test_split_and_tombstone(self) -> None:
        docs = DocService()
        docs.create_from_first_answer(self.rid, "整块", activate=True)
        docs.split_block(self.rid, "B01", ["主片段", "新片段"])
        doc = docs.require_active(self.rid)
        self.assertEqual(doc.get_block("B01").text, "主片段")
        new_ids = [b.block_id for b in doc.active_blocks() if b.split_from == "B01"]
        self.assertEqual(len(new_ids), 1)
        docs.apply_replace(self.rid, "B01", "")
        doc = docs.require_active(self.rid)
        self.assertIn("B01", doc.tombstones)
        ok, _ = docs.can_patch_target(self.rid, "B01")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
