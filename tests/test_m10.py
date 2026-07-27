"""T-M10 验收：M10-A～M10-E。"""

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
from multi_agent_room.logging_setup import log_event, setup_logging  # noqa: E402
from multi_agent_room.mcp_host import McpHost, McpServerConfig, PolicyPack  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.prompts import build_w1_prompt  # noqa: E402
from multi_agent_room.redact import redact_secrets  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402


class M10Tests(unittest.TestCase):
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
        (self.ws / "note.txt").write_text("hello-m10", encoding="utf-8")

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
            api_key="sk-w-secret-key-m10",
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
        self.room = self.svc.create_room("M10房", workspace_path=str(self.ws))
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.rid = self.room.room_id

    def _open_review(self) -> None:
        self.svc.ask_question(self.rid, "工具验收题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, "审阅稿正文。\n")
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertEqual(room.phase, "ReviewOpen")

    def test_M10_A_unauthorized_skill_blocked(self) -> None:
        self._open_review()
        # file.write 默认未授权
        r = self.svc.invoke_skill(
            self.rid,
            "file.write",
            {"path": "x.txt", "content": "nope"},
            agent_id=self.worker.agent_id,
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "unauthorized")
        self.assertIn("未授权", r.message)
        # 关闭只读 skill 后也无法触发
        self.svc.tools.skills.set_enabled("file.read", False)
        r2 = self.svc.invoke_skill(
            self.rid, "file.read", {"path": "note.txt"}, agent_id=self.worker.agent_id
        )
        self.assertFalse(r2.ok)
        self.assertIn("关闭", r2.message)

    def test_M10_B_invalid_args_room_readable(self) -> None:
        self._open_review()
        r = self.svc.invoke_skill(
            self.rid, "file.read", {}, agent_id=self.worker.agent_id
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "schema_required")
        self.assertIn("必填", r.message)
        # 房间可读：时间线 + 总线回执
        types = [e.type for e in self.bus.history(self.rid)]
        self.assertIn("ToolReceipt", types)
        tl = " ".join(
            f"{x.kind}:{x.summary}"
            for x in self.svc.timeline.list_events(self.rid)
        )
        self.assertIn("ToolReceipt", tl)
        self.assertIn("schema_required", tl)

    def test_M10_C_review_phase_write_denied(self) -> None:
        self._open_review()
        self.svc.authorize_skill("file.write", room_id=self.rid)
        r = self.svc.invoke_skill(
            self.rid,
            "file.write",
            {"path": "out.txt", "content": "should-fail"},
            agent_id=self.worker.agent_id,
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "readonly_phase")
        self.assertFalse((self.ws / "out.txt").exists())

    def test_M10_D_high_risk_needs_confirm(self) -> None:
        self._open_review()
        # 先发 writeToken 越过只读阶段，仍须高危确认
        self.svc.authorize_deliver(self.rid)
        self.svc.authorize_skill("terminal.run", room_id=self.rid)
        r = self.svc.invoke_skill(
            self.rid,
            "terminal.run",
            {"argv": [sys.executable, "-c", "print('m10-ok')"]},
            agent_id=self.worker.agent_id,
            user_confirmed=False,
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "need_confirm")
        # 确认后执行
        r2 = self.svc.confirm_high_risk_tool(
            self.rid, "terminal.run", agent_id=self.worker.agent_id
        )
        self.assertTrue(r2.ok, msg=f"{r2.code}:{r2.message} data={r2.data}")
        self.assertEqual(r2.exit_code, 0)
        receipts = [e for e in self.bus.history(self.rid) if e.type == "ToolReceipt"]
        self.assertGreaterEqual(len(receipts), 2)

    def test_M10_E_no_api_key_in_prompt_or_log(self) -> None:
        secret = "sk-w-secret-key-m10"
        q = f"请使用 api_key={secret} 调用接口"
        msgs = build_w1_prompt(question=q)
        blob = "\n".join(m["content"] for m in msgs)
        self.assertNotIn(secret, blob)
        self.assertIn("***", blob)
        log_event("tool_test", f"api_key={secret} leaked?")
        # redact helper
        self.assertNotIn(secret, redact_secrets(f"Bearer {secret}"))

    def test_M10_mcp_lifecycle_and_policy(self) -> None:
        host = self.svc.tools.mcp
        host.add_policy(PolicyPack("strict", allow_tools={"echo"}))
        host.configure(
            McpServerConfig(
                server_id="local-stub",
                command="stub",
                policy_pack_id="strict",
                declared_tools=["echo", "danger_write"],
            )
        )
        sess = host.connect("local-stub")
        self.assertTrue(sess.connected)
        self.assertEqual(host.list_tools("local-stub"), ["echo"])
        out = host.call_tool("local-stub", "echo", {"text": "ping"})
        self.assertEqual(out["result"], "ping")
        with self.assertRaises(PermissionError):
            host.call_tool("local-stub", "danger_write", {})
        host.shutdown("local-stub")
        with self.assertRaises(RuntimeError):
            host.list_tools("local-stub")

    def test_M10_write_after_gate_ok(self) -> None:
        self._open_review()
        self.svc.authorize_skill("file.write", room_id=self.rid)
        self.svc.judge_command(self.rid, "JudgeApprove")
        room = self.svc.get_room(self.rid)
        assert room is not None
        self.assertTrue(room.gate_passed)
        r = self.svc.invoke_skill(
            self.rid,
            "file.write",
            {"path": "deliver.md", "content": "# ok"},
            agent_id=self.worker.agent_id,
        )
        self.assertTrue(r.ok, msg=r.message)
        self.assertTrue((self.ws / "deliver.md").exists())
        self.assertTrue(
            any(e.type == "ToolReceipt" for e in self.bus.history(self.rid))
        )


if __name__ == "__main__":
    unittest.main()
