"""T-M11 验收：M11-A～M11-E。"""

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
from multi_agent_room.skeleton_gate import SkeletonQuota, check_skeleton  # noqa: E402


class M11Tests(unittest.TestCase):
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
        self.room = self.svc.create_room("M11房", workspace_path=str(self.ws))
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.rid = self.room.room_id

    def _roles(self) -> None:
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )

    def test_M11_A_t0_skip_exec_still_final(self) -> None:
        self.svc.ask_question(self.rid, "纯问答不需要执行补充")
        self._roles()
        t0 = self.svc.run_exec_t0(self.rid)
        self.assertEqual(t0.mode, "T0")
        self.assertEqual(t0.code, "skipped")
        self.assertFalse(self.svc.exec_svc.is_enabled(self.rid))
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, "直接答复正文。")
        self.svc.judge_command(self.rid, "JudgeApprove")
        self.svc.commit_final_reply(self.rid)
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertTrue(room.gate_passed)
        self.assertTrue(room.final_reply)

    def test_M11_B_static_fail_no_sandbox_success(self) -> None:
        self.svc.ask_question(self.rid, "跑一段 python 代码")
        self._roles()
        bad = "def broken(\n  return 1\n"
        r = self.svc.run_exec_t2(self.rid, bad, filename="main.py", goal="运行")
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "syntax_error")
        self.assertIsNotNone(r.static)
        assert r.static is not None
        self.assertFalse(r.static.ok)
        self.assertIsNone(r.sandbox)  # 未进沙盒

    def test_M11_C_sandbox_fail_has_stack_feedback(self) -> None:
        self.svc.ask_question(self.rid, "python 脚本自测")
        self._roles()
        src = "print(1/0)\n"
        r = self.svc.run_exec_t2(self.rid, src, filename="boom.py", goal="不崩溃")
        self.assertFalse(r.ok)
        self.assertIsNotNone(r.sandbox)
        assert r.sandbox is not None
        self.assertFalse(r.sandbox.ok)
        self.assertTrue(r.sandbox.stack or r.sandbox.stderr)
        self.assertIsNotNone(r.feedback)
        assert r.feedback is not None
        self.assertIn("ZeroDivision", r.feedback.error_type + r.feedback.stack)
        self.assertIn("刚刚写的代码报错", r.feedback.prompt_text())
        self.assertIn("stack=", r.feedback.prompt_text())

    def test_M11_D_empty_result_review_fail(self) -> None:
        rev = self.svc.review_exec_result(goal="输出答案", current_result="", gap="")
        self.assertEqual(rev.verdict, "fail")
        self.assertEqual(rev.current_result, "")
        # T2 空输出
        self.svc.ask_question(self.rid, "python 空输出")
        self._roles()
        r = self.svc.run_exec_t2(
            self.rid, "pass\n", filename="empty.py", goal="有输出"
        )
        self.assertIsNotNone(r.review)
        assert r.review is not None
        self.assertEqual(r.review.verdict, "fail")

    def test_M11_E_no_m7_cannot_complete(self) -> None:
        self.svc.ask_question(self.rid, "未 Final 不能完成")
        self._roles()
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, "过程稿")
        ok, msg = self.svc.mark_task_complete(self.rid)
        self.assertFalse(ok)
        self.assertIn("不能标任务完成", msg)
        self.assertFalse(self.svc.exec_svc.is_task_complete(self.rid))
        # 走完 M7 后可标完成
        self.svc.judge_command(self.rid, "JudgeApprove")
        self.svc.commit_final_reply(self.rid)
        ok2, msg2 = self.svc.mark_task_complete(self.rid)
        self.assertTrue(ok2, msg=msg2)
        self.assertTrue(self.svc.exec_svc.is_task_complete(self.rid))

    def test_M11_skeleton_force_reject(self) -> None:
        huge = "\n".join(f"line-{i}" for i in range(450))
        sk = check_skeleton(huge, quota=SkeletonQuota(max_lines=400, warn_only=False))
        self.assertTrue(sk.rejected)
        self.svc.ask_question(self.rid, "超长拒收")
        self._roles()
        with self.assertRaises(ValueError) as ctx:
            self.svc.submit_first_answer(self.rid, self.worker.agent_id, huge)
        self.assertIn("骨架配额", str(ctx.exception))

    def test_M11_doc_profile_no_forced_web(self) -> None:
        self.svc.ask_question(self.rid, "写一篇 markdown 说明文档总结")
        t0 = self.svc.run_exec_t0(self.rid)
        assert t0.profile is not None
        self.assertEqual(t0.profile.task_kind, "doc")
        self.assertFalse(t0.profile.needs_web)


if __name__ == "__main__":
    unittest.main()
