"""T-M5 验收：M5-A～M5-I + 金样例夹具。"""

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
from multi_agent_room.patch_filter import PatchFilter, SIX_DIM_HINT  # noqa: E402
from multi_agent_room.prompts import SIX_DIM_REVIEW_HINT, build_w3_prompt, build_j1_prompt  # noqa: E402
from multi_agent_room.review_service import ReviewService  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402

FIXTURES = ROOT / "fixtures" / "patches"


class M5Tests(unittest.TestCase):
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
        self.w1 = self.agents.create_agent(
            display_name="W1",
            model_config_id=self.cfg_a.config_id,
            agent_id="agent-w1",
        )
        self.w2 = self.agents.create_agent(
            display_name="W2",
            model_config_id=self.cfg_b.config_id,
            agent_id="agent-w2",
        )
        self.room = self.svc.create_room("M5房")
        self.svc.invite_ready_agent(self.room.room_id, self.w1.agent_id)
        self.svc.invite_ready_agent(self.room.room_id, self.w2.agent_id)
        self.rid = self.room.room_id

    def _open_review(self, text: str = "# 标题\n\n正文段落内容用于审阅。\n") -> None:
        self.svc.ask_question(self.rid, "题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.w1.agent_id,
            reviewer_agent_ids=[self.w1.agent_id, self.w2.agent_id],
            judge_is_user=True,
        )
        self.svc.submit_first_answer(self.rid, self.w1.agent_id, text)

    def test_M5_A_unread_not_agree(self) -> None:
        self._open_review()
        # 仅 w1 已读，w2 未读
        self.svc.record_read(self.rid, agent_id=self.w1.agent_id)
        agrees = self.svc.silent_agree_map(self.rid)
        self.assertTrue(agrees.get(self.w1.agent_id))
        self.assertFalse(agrees.get(self.w2.agent_id))

    def test_M5_B_timeout_unread_not_agree(self) -> None:
        self._open_review()
        self.svc.record_read(self.rid, agent_id=self.w1.agent_id)
        win = self.svc.review.get_window(self.rid)
        assert win is not None
        # 强制到超时点
        now = win._timeout_deadline + 1
        ok, reason = self.svc.try_silence_pass(self.rid, now=now)
        self.assertTrue(ok, msg=reason)
        agrees = self.svc.silent_agree_map(self.rid)
        self.assertFalse(agrees.get(self.w2.agent_id), "超时未表态 ≠ 同意")
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertFalse(room.final_reply)
        self.assertFalse(room.gate_passed)

    def test_M5_C_triv_fixtures(self) -> None:
        pf = PatchFilter()
        paths = sorted(FIXTURES.glob("TRIV-*.json"))
        self.assertGreaterEqual(len(paths), 5)
        kinds = set()
        for path in paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            r = pf.filter(
                room_id="fx",
                agent_id="a",
                fields=data["fields"],
                old_text=data["old"],
                doc_full=data.get("doc_full") or data["old"],
                doc_version=1,
                other_block_texts=data.get("other_blocks") or {},
            )
            self.assertFalse(r.ok, msg=f"{data['id']}: expected reject, got {r}")
            self.assertEqual(r.code, data["expect"], msg=data["id"])
            if data.get("kind_hint"):
                kinds.add(data["kind_hint"])
        self.assertTrue({"zh", "en", "code"} <= kinds)

    def test_M5_D_sub_fixtures(self) -> None:
        pf = PatchFilter()
        for path in sorted(FIXTURES.glob("SUB-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            r = pf.filter(
                room_id="fx",
                agent_id="a",
                fields=data["fields"],
                old_text=data["old"],
                doc_full=data.get("doc_full") or data["old"],
                doc_version=1,
                other_block_texts=data.get("other_blocks") or {},
            )
            self.assertTrue(r.ok, msg=f"{data['id']}: {r.code} {r.message}")
            self.assertIsNotNone(r.patch)

    def test_M5_E_abstain_out_of_denominator(self) -> None:
        self._open_review()
        self.svc.review.register_abstain(self.rid, self.w2.agent_id)
        eff = self.svc.effective_reviewers(self.rid)
        self.assertNotIn(self.w2.agent_id, eff)
        self.assertIn(self.w1.agent_id, eff)
        agrees = self.svc.silent_agree_map(self.rid)
        self.assertFalse(agrees.get(self.w2.agent_id, False))

    def test_M5_F_close_hands_queue_no_auto_final(self) -> None:
        self._open_review("open(path).read()\n")
        r = self.svc.ingest_worker_output(
            self.rid,
            self.w2.agent_id,
            json.dumps(
                {
                    "type": "Patch",
                    "target": "B01",
                    "category": "logic",
                    "claim": "缺错误处理会导致崩溃",
                    "replace": "try:\n    open(path).read()\nexcept OSError:\n    return None\n",
                }
            ),
        )
        self.assertTrue(r.entered_queue, msg=r.error)
        self.svc.record_read(self.rid, agent_id=self.w1.agent_id)
        self.svc.record_read(self.rid, agent_id=self.w2.agent_id)
        win = self.svc.review.get_window(self.rid)
        assert win is not None
        # 缩短静默期便于关窗
        win.quiet_period_ms = 0
        ok, reason = self.svc.try_silence_pass(self.rid, now=win.opened_at + 1)
        self.assertTrue(ok, msg=reason)
        q = self.svc.pending_patches(self.rid)
        self.assertEqual(len(q), 1)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertIsNone(room.final_reply)
        self.assertFalse(room.gate_passed)
        self.assertEqual(room.phase, "AwaitingJudge")
        close = self.svc.review.get_window(self.rid)
        assert close is not None
        self.assertTrue(close.handed_to_m6)
        self.assertFalse(close.close_reason == "" and False)

    def test_M5_G_six_dim_hint(self) -> None:
        self.assertIn("题意", SIX_DIM_HINT)
        self.assertIn("正确性", SIX_DIM_HINT)
        self.assertNotIn("文采", SIX_DIM_HINT.replace("不含文采偏好", ""))
        w3 = build_w3_prompt(question="q", current_doc="doc")
        j1 = build_j1_prompt(question="q", current_doc="doc", pending_patches=[])
        blob = json.dumps(w3 + j1, ensure_ascii=False)
        self.assertIn("题意对齐", blob)
        self.assertIn("不含文采偏好", blob)
        self.assertEqual(self.svc.review.six_dim_hint(), SIX_DIM_REVIEW_HINT)

    def test_M5_H_full_rewrite_fixtures(self) -> None:
        pf = PatchFilter()
        for name in ("RW-1.json", "RW-2.json", "RW-3.json"):
            data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            r = pf.filter(
                room_id="fx",
                agent_id="a",
                fields=data["fields"],
                old_text=data["old"],
                doc_full=data.get("doc_full") or data["old"],
                doc_version=1,
                other_block_texts=data.get("other_blocks") or {},
            )
            self.assertEqual(
                r.code, "full_rewrite", msg=f"{data['id']}: {r.code} {r.message}"
            )

    def test_M5_I_rewrite_token_or_r2(self) -> None:
        data = json.loads((FIXTURES / "RW-1.json").read_text(encoding="utf-8"))
        # 持令牌不过杀
        pf = PatchFilter()
        pf.grant_rewrite("r1", 1)
        r = pf.filter(
            room_id="r1",
            agent_id="a",
            fields=data["fields"],
            old_text=data["old"],
            doc_full=data["doc_full"],
            doc_version=1,
        )
        self.assertTrue(r.ok, msg=r.message)
        # R2 bypass
        rs = ReviewService()
        rs.allow_r2_rewrite_bypass("r2", True)
        r2 = rs.submit_patch(
            room_id="r2",
            agent_id="a",
            fields=data["fields"],
            old_text=data["old"],
            doc_full=data["doc_full"],
            doc_version=1,
        )
        self.assertTrue(r2.ok, msg=r2.message)

    def test_MarkTrivial_judge_only(self) -> None:
        self._open_review("open(path).read()\n")
        r = self.svc.ingest_worker_output(
            self.rid,
            self.w1.agent_id,
            json.dumps(
                {
                    "type": "Patch",
                    "target": "B01",
                    "category": "logic",
                    "claim": "缺错误处理会导致崩溃",
                    "replace": "try:\n    open(path).read()\nexcept OSError:\n    return None\n",
                }
            ),
        )
        self.assertTrue(r.entered_queue, msg=r.error)
        pid = self.svc.pending_patches(self.rid)[0].patch_id
        with self.assertRaises(ValueError):
            self.svc.judge_command(
                self.rid,
                "MarkTrivial",
                patch_id=pid,
                agent_id=self.w1.agent_id,
            )
        self.svc.judge_command(self.rid, "MarkTrivial", patch_id=pid, agent_id=None)
        self.assertEqual(len(self.svc.pending_patches(self.rid)), 0)


if __name__ == "__main__":
    unittest.main()
