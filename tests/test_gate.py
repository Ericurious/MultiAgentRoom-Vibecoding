"""T-GATE：门禁联调 E2E-01～17（自动 AgentRuntime + 注入响应）。"""

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
from multi_agent_room.room_service import RoomService  # noqa: E402
from multi_agent_room.worker_runtime import AgentRuntime  # noqa: E402


class GateTests(unittest.TestCase):
    """按 tasks.md §12 GWT 表逐项验收。"""

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
            model_id="worker-m",
            api_key="sk-w",
        )
        self.cfg_j = self.models.add_model(
            display_name="JudgeModel",
            base_url="https://j.example/v1",
            model_id="judge-m",
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
        self.room = self.svc.create_room("GATE房")
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.svc.invite_ready_agent(self.room.room_id, self.judge.agent_id)
        self.rid = self.room.room_id

    def _assign(self, *, judge_user: bool = False) -> None:
        if judge_user:
            self.agents.user_assign_roles(
                self.rid,
                first_answerer_agent_id=self.worker.agent_id,
                judge_is_user=True,
                reviewer_agent_ids=[self.worker.agent_id],
            )
        else:
            self.agents.user_assign_roles(
                self.rid,
                first_answerer_agent_id=self.worker.agent_id,
                judge_agent_id=self.judge.agent_id,
                reviewer_agent_ids=[self.worker.agent_id],
            )

    def _patch_json(
        self, target: str, replace: str, claim: str = "缺错误处理会导致崩溃", **extra
    ) -> str:
        return json.dumps(
            {
                "type": "Patch",
                "target": target,
                "category": extra.get("category", "logic"),
                "claim": claim,
                "replace": replace,
            },
            ensure_ascii=False,
        )

    def _ingest_w(self, text: str):
        return self.svc.ingest_worker_output(self.rid, self.worker.agent_id, text)

    def test_E2E01_GATE01_two_models_review(self) -> None:
        self.svc.ask_question(self.rid, "双模型联调")
        self._assign()
        sw = self.agents.sessions.open(self.worker.agent_id, self.rid)
        sj = self.agents.sessions.open(self.judge.agent_id, self.rid)
        self.assertNotEqual(sw.session_id, sj.session_id)
        w1 = self.rt.run_w1(self.rid, response="# 方案\n\n完整首答正文。\n")
        self.assertTrue(w1.ok)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ReviewOpen")
        self.assertTrue(room.review_window_open)
        self.assertNotEqual(self.cfg_w.config_id, self.cfg_j.config_id)

    def test_E2E02_GATE02_trivial_rejected(self) -> None:
        self.svc.ask_question(self.rid, "润色拒收")
        self._assign()
        self.rt.run_w1(self.rid, response="很好\n")
        phase_before = self.svc.get_room(self.rid).phase  # type: ignore[union-attr]
        r = self._ingest_w(
            self._patch_json(
                "B01",
                "非常好",
                claim="润色",
                category="other",
            )
        )
        self.assertFalse(r.entered_queue)
        err = (r.error or "").lower()
        self.assertTrue(
            "triv" in err or "trivial" in err,
            msg=r.error,
        )
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, phase_before)
        self.assertTrue(room.review_window_open)

    def test_E2E03_GATE03_single_patch_confirm_approve(self) -> None:
        self.svc.ask_question(self.rid, "单补丁路径")
        self._assign()
        self.rt.run_w1(self.rid, response="原始正文待修订。\n")
        before = self.svc.docs.require_active(self.rid).get_block("B01").text
        r = self.rt.run_w2(
            self.rid,
            response=self._patch_json("B01", "合入后正文含错误处理。"),
        )
        self.assertTrue(r.ok and r.proto and r.proto.entered_queue)
        self.assertEqual(
            self.svc.docs.require_active(self.rid).get_block("B01").text, before
        )
        self.assertNotEqual(self.svc.get_room(self.rid).phase, "Final")  # type: ignore[union-attr]
        pid = self.svc.pending_patches(self.rid)[0].patch_id
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        self.assertIn(
            "合入后", self.svc.docs.require_active(self.rid).get_block("B01").text
        )
        self.assertEqual(self.svc.get_room(self.rid).phase, "ConfirmOpen")  # type: ignore[union-attr]
        self.svc._make_confirm_ready(self.rid)
        self.assertIsNone(self.svc.get_room(self.rid).final_reply)  # type: ignore[union-attr]
        j = self.rt.run_j1(
            self.rid,
            self.judge.agent_id,
            response=json.dumps(
                {"type": "JudgeApprove", "agent_id": self.judge.agent_id}
            ),
        )
        self.assertTrue(j.ok, msg=j.error)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "Final")
        self.svc.commit_final_reply(self.rid)
        self.assertTrue(room.final_reply)

    def test_E2E04_17_GATE04_merge_conflict(self) -> None:
        self.svc.ask_question(self.rid, "冲突合并")
        self._assign()
        self.rt.run_w1(self.rid, response="冲突块原文足够长。\n")
        self._ingest_w(
            self._patch_json("B01", "方案甲：本地校验与超时。", claim="改控制流防止崩溃")
        )
        self.svc.ingest_worker_output(
            self.rid,
            self.judge.agent_id,
            self._patch_json("B01", "方案乙：远程重试策略。", claim="改控制流防止崩溃"),
        )
        with self.assertRaises(ValueError):
            self.svc.judge_command(
                self.rid,
                "MergeConflict",
                target="B01",
                strategy="rewrite",
                newText="合并稿",
                reason="",
            )
        pids = [p.patch_id for p in self.svc.pending_patches(self.rid) if p.target == "B01"]
        self.assertGreaterEqual(len(pids), 2)
        self.svc.judge_command(
            self.rid,
            "MergeConflict",
            target="B01",
            strategy="chooseA",
            patch_id=pids[0],
            reason="选甲更贴原话",
        )
        self.assertEqual(len(self.svc.judge.list_merge_records(self.rid)), 1)
        self.assertIn("方案甲", self.svc.docs.require_active(self.rid).get_block("B01").text)

    def test_E2E05_GATE05_r1_two_blocks(self) -> None:
        self.svc.ask_question(self.rid, "R1 两块")
        self._assign()
        self.rt.run_w1(
            self.rid,
            response="# A\n\n第一块内容。\n\n# B\n\n第二块内容。\n\n# C\n\n第三块内容。\n",
        )
        doc = self.svc.docs.require_active(self.rid)
        ids = [b.block_id for b in doc.active_blocks()]
        self.assertGreaterEqual(len(ids), 3)
        b1, b2, b3 = ids[0], ids[1], ids[2]
        self.svc.judge_command(self.rid, "R1", target=b1, reason="缺边界")
        self.svc.judge_command(self.rid, "R1", target=b2, reason="缺校验")
        with self.assertRaises(ValueError):
            self.svc.judge_command(
                self.rid, "JudgeApprove", agent_id=self.judge.agent_id
            )
        # 未点名块锁定
        bad = self._ingest_w(
            self._patch_json(b3, "非法改第三块含错误处理。")
        )
        self.assertFalse(bad.entered_queue)
        self.assertIn("锁定", bad.error or "")
        # 重交两块并合入
        for bid, text in (
            (b1, "第一块重交含错误处理。"),
            (b2, "第二块重交含错误处理。"),
        ):
            r = self._ingest_w(self._patch_json(bid, text))
            self.assertTrue(r.entered_queue, msg=r.error)
            pid = [p for p in self.svc.pending_patches(self.rid) if p.target == bid][
                -1
            ].patch_id
            self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        self.assertFalse(self.svc.judge.has_open_rejects(self.rid))
        self.assertEqual(self.svc.get_room(self.rid).phase, "ConfirmOpen")  # type: ignore[union-attr]
        self.svc._make_confirm_ready(self.rid)
        self.svc.judge_command(
            self.rid, "JudgeApprove", agent_id=self.judge.agent_id
        )
        self.assertEqual(self.svc.get_room(self.rid).phase, "Final")  # type: ignore[union-attr]

    def test_E2E06_GATE06_r2_quality_and_void(self) -> None:
        self.svc.ask_question(self.rid, "R2 质检")
        self._assign()
        self.rt.run_w1(self.rid, response="将被作废的旧稿")
        old = self.svc.docs.require_active(self.rid).doc_id
        with self.assertRaises(ValueError):
            self.svc.judge_command(self.rid, "R2", wrong_direction="偏了")
        self.svc.judge_command(
            self.rid,
            "R2",
            user_goal_ref="R2 质检",
            wrong_direction="方向偏离用户目标",
            required_direction="按原话重做首答",
            keep=["术语"],
            discard=["旧结论"],
        )
        self.assertIsNone(self.svc.docs.store.get_active(self.rid))
        self.assertTrue(self.svc.docs.requires_new_first_answer(self.rid))
        hist = self.svc.docs.store._history.get(self.rid) or []
        self.assertTrue(any(d.doc_id == old and d.status == "voided" for d in hist))
        self.rt.run_w1(self.rid, response="R2 后新首答正文")
        self.assertEqual(self.svc.get_room(self.rid).phase, "ReviewOpen")  # type: ignore[union-attr]

    def test_E2E07_GATE07_r3_changed_set_then_approve(self) -> None:
        self.svc.ask_question(self.rid, "R3 路径")
        self._assign()
        self.rt.run_w1(
            self.rid,
            response="# A\n\n第一块。\n\n# B\n\n第二块。\n",
        )
        self.svc.judge_command(
            self.rid,
            "R3",
            target="B01",
            replace="第一块微调：统一命名并补空指针检查。",
            claim="命名统一",
        )
        self.assertEqual(self.svc.get_room(self.rid).phase, "ConfirmOpen")  # type: ignore[union-attr]
        self.assertEqual(self.svc.confirm.changed_set(self.rid), {"B01"})
        bad = self._ingest_w(
            self._patch_json("B02", "非法改第二块含错误处理。")
        )
        self.assertFalse(bad.entered_queue)
        self.assertIn("ChangedSet", bad.error or "")
        self.svc._make_confirm_ready(self.rid)
        self.svc.judge_command(
            self.rid, "JudgeApprove", agent_id=self.judge.agent_id
        )
        self.assertEqual(self.svc.get_room(self.rid).phase, "Final")  # type: ignore[union-attr]

    def test_E2E08_GATE08_degraded_user_judge(self) -> None:
        # 仅工人一模型；评议降级用户
        self.svc.ask_question(self.rid, "降级A")
        self._assign(judge_user=True)
        self.rt.run_w1(self.rid, response="降级正文")
        bad = self.svc.ingest_judge_output(
            self.rid,
            self.worker.agent_id,
            json.dumps({"type": "JudgeApprove", "agent_id": self.worker.agent_id}),
        )
        self.assertFalse(bad.ok)
        self.svc.judge_command(self.rid, "JudgeApprove", agent_id=None)
        self.assertEqual(self.svc.get_room(self.rid).phase, "Final")  # type: ignore[union-attr]

    def test_E2E09_GATE09_silent_path(self) -> None:
        self.svc.ask_question(self.rid, "静默通过")
        self._assign()
        self.rt.run_w1(self.rid, response="静默首答")
        self.rt.run_w2(
            self.rid,
            response=json.dumps(
                {
                    "type": "SilentCheckPass",
                    "agent_id": self.worker.agent_id,
                    "version": 1,
                    "doc_id": "d1",
                }
            ),
        )
        self.assertFalse(self.svc.confirm.get(self.rid).had_write)
        j = self.rt.run_j1(
            self.rid,
            self.judge.agent_id,
            response=json.dumps(
                {"type": "JudgeApprove", "agent_id": self.judge.agent_id}
            ),
        )
        self.assertTrue(j.ok, msg=j.error)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "Final")
        self.assertNotEqual(room.phase, "ConfirmOpen")

    def test_E2E12_GATE10_worker_judge_split(self) -> None:
        self.svc.ask_question(self.rid, "分工")
        self._assign()
        self.rt.run_w1(self.rid, response="首答正文")
        patch = self.rt.run_w2(
            self.rid,
            response=self._patch_json("B01", "加校验与错误处理。", claim="缺校验会导致崩溃"),
        )
        self.assertTrue(patch.proto and patch.proto.entered_queue)
        bad = self.svc.ingest_judge_output(
            self.rid,
            self.worker.agent_id,
            json.dumps({"type": "JudgeApprove", "agent_id": self.worker.agent_id}),
        )
        self.assertFalse(bad.ok)
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
        self.assertEqual(self.svc.get_room(self.rid).phase, "Final")  # type: ignore[union-attr]

    def test_E2E13_GATE11_interrupt_resume(self) -> None:
        self.svc.ask_question(self.rid, "打断")
        self._assign()
        self.rt.run_w1(self.rid, response="审阅中正文")
        self.svc.interrupt(self.rid)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertTrue(room.frozen)
        self.assertTrue(room.review_window_open)
        ok, msg = self.svc.try_silence_pass(self.rid)
        self.assertFalse(ok)
        self.svc.resume(self.rid)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertFalse(room.frozen)
        self.assertEqual(room.phase, "ReviewOpen")

    def test_E2E14_GATE12_changed_set_reject(self) -> None:
        self.svc.ask_question(self.rid, "ChangedSet")
        self._assign()
        self.rt.run_w1(
            self.rid,
            response="# X\n\n块十二内容。\n\n# Y\n\n块三内容。\n",
        )
        # 人工设定确认轮 ChangedSet={B01} 模拟 B12 语义
        doc = self.svc.docs.require_active(self.rid)
        b_in, b_out = doc.active_blocks()[0].block_id, doc.active_blocks()[1].block_id
        r = self._ingest_w(self._patch_json(b_in, "块十二修订含错误处理。"))
        self.assertTrue(r.entered_queue, msg=r.error)
        pid = self.svc.pending_patches(self.rid)[0].patch_id
        self.svc.judge_command(self.rid, "Accept", patch_id=pid)
        self.assertEqual(self.svc.confirm.changed_set(self.rid), {b_in})
        bad = self._ingest_w(self._patch_json(b_out, "块三非法修订含错误处理。"))
        self.assertFalse(bad.entered_queue)
        self.assertIn("ChangedSet", bad.error or "")

    def test_E2E15_GATE13_idle_no_w1(self) -> None:
        calls: list[str] = []

        def caller(msgs, aid):
            calls.append(aid)
            return "nope"

        self.rt.caller = caller
        r = self.rt.run_w1(self.rid)
        self.assertFalse(r.ok)
        self.assertFalse(r.model_called)
        self.assertEqual(calls, [])

    def test_E2E16_GATE14_full_rewrite_reject(self) -> None:
        self.svc.ask_question(self.rid, "全文重写")
        self._assign()
        long_old = (
            "本节说明安装步骤：首先下载安装包，然后解压到目标目录，"
            "接着配置环境变量，最后验证命令是否可用。还包含注意事项与排错建议。"
        )
        self.rt.run_w1(self.rid, response=long_old)
        r = self._ingest_w(
            self._patch_json(
                "B01",
                "完全不同的长文：架构决策、微服务拆分、数据库选型、缓存策略、"
                "观测性与发布流程、容量规划与成本模型，内容与原文几乎无重叠。",
                claim="重写整节",
                category="scheme",
            )
        )
        self.assertFalse(r.entered_queue)
        err = (r.error or "").lower()
        self.assertTrue(
            "full_rewrite" in err or "rw-" in err,
            msg=r.error,
        )


if __name__ == "__main__":
    unittest.main()
