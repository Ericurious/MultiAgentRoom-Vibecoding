"""T-SEC 验收：SEC-A～D 及 SEC-02～05。"""

from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_room.adapters import ProbeResult, resolve_api_key  # noqa: E402
from multi_agent_room.agent_service import AgentService  # noqa: E402
from multi_agent_room.event_bus import EventBus  # noqa: E402
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.prompts import build_w1_prompt  # noqa: E402
from multi_agent_room.room_service import RoomService  # noqa: E402
from multi_agent_room.sec_guard import (  # noqa: E402
    SecViolation,
    assert_sandbox_not_workspace,
    assert_unique_secret_store,
)
from multi_agent_room.secret_store import SecretStore, get_secret_store  # noqa: E402


class SecTests(unittest.TestCase):
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
        self.secret = "sk-sec-unique-plain-key-xyz"
        self.cfg = self.models.add_model(
            display_name="S",
            base_url="https://s.example/v1",
            model_id="s",
            api_key=self.secret,
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "ok"),
        ):
            self.models.probe(self.cfg.config_id)
        self.worker = self.agents.create_agent(
            display_name="S",
            model_config_id=self.cfg.config_id,
            agent_id="agent-s",
        )
        self.room = self.svc.create_room("SEC房", workspace_path=str(self.ws))
        self.svc.invite_ready_agent(self.room.room_id, self.worker.agent_id)
        self.rid = self.room.room_id

    def test_SEC_A_disk_no_plaintext_key(self) -> None:
        store = get_secret_store()
        self.assertFalse(store.contains_plaintext_on_disk(self.secret))
        models = (Path(self._tmpdir.name) / "MultiAgentRoom" / "config" / "models.json")
        # APPDATA patch: get_config_dir uses APPDATA/MultiAgentRoom
        from multi_agent_room.paths import get_config_dir

        models = get_config_dir() / "models.json"
        text = models.read_text(encoding="utf-8")
        self.assertNotIn(self.secret, text)
        self.assertIn("api_key_ref", text)
        vault = get_config_dir() / "secrets.vault"
        self.assertTrue(vault.exists())
        self.assertNotIn(self.secret, vault.read_text(encoding="utf-8", errors="ignore"))

    def test_SEC_B_shared_doc_and_memory_reject_key(self) -> None:
        self.svc.ask_question(self.rid, "安全验收题")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )
        with self.assertRaises(SecViolation):
            self.svc.submit_first_answer(
                self.rid,
                self.worker.agent_id,
                f"正文含密钥 api_key={self.secret}",
            )
        with self.assertRaises(SecViolation):
            self.svc.write_shared_memory(
                self.rid, "todo", f"记住 Bearer {self.secret}"
            )
        with self.assertRaises(SecViolation):
            self.svc.write_private_memory(
                self.rid, self.worker.agent_id, f"sk-{self.secret[3:]}"
            )
        # 正常稿可入库且搜不到 Key
        self.svc.submit_first_answer(
            self.rid, self.worker.agent_id, "正常共享稿不含密钥。"
        )
        doc = self.svc.docs.require_active(self.rid)
        self.assertNotIn(self.secret, doc.full_text())

    def test_SEC_C_prompt_strips_key(self) -> None:
        msgs = build_w1_prompt(question=f"请用 api_key={self.secret} 调用")
        blob = "\n".join(m["content"] for m in msgs)
        self.assertNotIn(self.secret, blob)
        self.assertIn("***", blob)

    def test_SEC_D_same_secret_store_singleton(self) -> None:
        a = get_secret_store()
        b = self.models.secrets
        c = assert_unique_secret_store(b)
        self.assertIs(a, b)
        self.assertIs(b, c)
        self.assertIsInstance(a, SecretStore)
        # M1 通过 resolve_api_key 读同一存储
        self.assertEqual(resolve_api_key(self.cfg), self.secret)
        # 代码双检：model_service 引用 get_secret_store
        import multi_agent_room.model_service as ms
        import multi_agent_room.adapters as ad

        self.assertIn("get_secret_store", inspect.getsource(ms))
        self.assertIn("get_secret_store", inspect.getsource(ad))
        # 禁止另建
        with self.assertRaises(SecViolation):
            assert_unique_secret_store(SecretStore())

    def test_SEC_04_high_risk_needs_confirm(self) -> None:
        self.svc.ask_question(self.rid, "高危确认")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )
        self.svc.submit_first_answer(self.rid, self.worker.agent_id, "稿")
        self.svc.authorize_deliver(self.rid)
        self.svc.authorize_skill("terminal.run", room_id=self.rid)
        r = self.svc.invoke_skill(
            self.rid,
            "terminal.run",
            {"argv": [sys.executable, "-c", "print(1)"]},
            agent_id=self.worker.agent_id,
            user_confirmed=False,
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "need_confirm")

    def test_SEC_05_sandbox_separated_from_workspace(self) -> None:
        self.svc.ask_question(self.rid, "python 沙盒分离")
        self.agents.user_assign_roles(
            self.rid,
            first_answerer_agent_id=self.worker.agent_id,
            judge_is_user=True,
            reviewer_agent_ids=[self.worker.agent_id],
        )
        r = self.svc.run_exec_t2(
            self.rid, "print('sec-ok')\n", filename="t.py", goal="sec-ok"
        )
        self.assertIsNotNone(r.sandbox)
        assert r.sandbox is not None
        assert_sandbox_not_workspace(r.sandbox.sandbox_dir, str(self.ws))
        # 显式重叠应失败
        with self.assertRaises(SecViolation):
            assert_sandbox_not_workspace(str(self.ws), str(self.ws))
        nested = self.ws / "nested-sandbox"
        nested.mkdir()
        with self.assertRaises(SecViolation):
            assert_sandbox_not_workspace(str(nested), str(self.ws))


if __name__ == "__main__":
    unittest.main()
