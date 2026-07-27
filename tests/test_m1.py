"""T-M1 / SEC-01 验收测试。"""

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
from multi_agent_room.logging_setup import setup_logging  # noqa: E402
from multi_agent_room.model_config import ModelConfigStore, models_file  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.secret_store import SecretStore, get_secret_store  # noqa: E402


class M1Tests(unittest.TestCase):
    def setUp(self) -> None:
        setup_logging()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # 将 AppData 定向到临时目录，避免污染真实配置
        self._patcher = patch.dict(
            "os.environ",
            {"APPDATA": self._tmpdir.name},
            clear=False,
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        # 重置 SecretStore 单例
        import multi_agent_room.secret_store as ss

        ss._STORE = None
        self.svc = ModelService()

    def test_M1_A_two_base_urls(self) -> None:
        a = self.svc.add_model(
            display_name="A",
            base_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            api_key="sk-test-aaa-secret",
        )
        b = self.svc.add_model(
            display_name="B",
            base_url="https://example.com/v1",
            model_id="other-model",
            api_key="sk-test-bbb-secret",
        )
        all_m = self.svc.list_models()
        self.assertEqual(len(all_m), 2)
        urls = {m.base_url for m in all_m}
        self.assertEqual(urls, {"https://api.openai.com/v1", "https://example.com/v1"})
        self.assertNotEqual(a.config_id, b.config_id)

    def test_M1_B_bad_baseurl_probe_failed_not_bindable(self) -> None:
        cfg = self.svc.add_model(
            display_name="Bad",
            base_url="http://127.0.0.1:1",
            model_id="x",
            api_key="sk-bad",
            timeout_ms=2000,
        )
        cfg2, result = self.svc.probe(cfg.config_id)
        self.assertFalse(result.ok)
        self.assertEqual(cfg2.status, "failed")
        self.assertIn(result.code, {"network", "timeout", "adapter"})
        self.assertTrue(result.ui_text)
        ok, reason = self.svc.can_bind(cfg.config_id)
        self.assertFalse(ok)
        self.assertIn("未就绪", reason)
        self.assertEqual(self.svc.list_bindable(), [])

    def test_M1_C_probe_ok_ready_bindable(self) -> None:
        cfg = self.svc.add_model(
            display_name="Good",
            base_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            api_key="sk-good-key",
        )

        def fake_probe(self_adapter, model_cfg, api_key):  # noqa: ANN001
            self.assertEqual(api_key, "sk-good-key")
            return ProbeResult(True, "ok", "ok", "探活成功")

        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            fake_probe,
        ):
            cfg2, result = self.svc.probe(cfg.config_id)
        self.assertTrue(result.ok)
        self.assertEqual(cfg2.status, "ready")
        ok, _ = self.svc.can_bind(cfg.config_id)
        self.assertTrue(ok)
        self.assertEqual(len(self.svc.list_bindable()), 1)

    def test_M1_D_persist_and_no_plaintext_key(self) -> None:
        secret = "sk-live-SHOULD-NOT-PLAINTEXT-xyz"
        cfg = self.svc.add_model(
            display_name="Persist",
            base_url="https://a.example/v1",
            model_id="m",
            api_key=secret,
        )
        # 重启语义：新 Service / 新 Store 读取磁盘
        import multi_agent_room.secret_store as ss

        ss._STORE = None
        svc2 = ModelService()
        again = svc2.store.get(cfg.config_id)
        self.assertIsNotNone(again)
        assert again is not None
        self.assertEqual(again.display_name, "Persist")
        self.assertEqual(again.api_key_ref, cfg.api_key_ref)
        # models.json 无明文
        models_text = models_file().read_text(encoding="utf-8")
        self.assertNotIn(secret, models_text)
        self.assertIn("api_key_ref", models_text)
        # vault 无明文
        store = get_secret_store()
        self.assertFalse(store.contains_plaintext_on_disk(secret))
        # 仍可解密取出
        self.assertEqual(store.get(again.api_key_ref), secret)

    def test_M1_E_disabled_not_selectable(self) -> None:
        cfg = self.svc.add_model(
            display_name="Dis",
            base_url="https://a.example/v1",
            model_id="m",
            api_key="sk-dis",
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "探活成功"),
        ):
            self.svc.probe(cfg.config_id)
        self.assertEqual(len(self.svc.list_bindable()), 1)
        self.svc.set_enabled(cfg.config_id, False)
        ok, reason = self.svc.can_bind(cfg.config_id)
        self.assertFalse(ok)
        self.assertIn("禁用", reason)
        self.assertEqual(self.svc.list_bindable(), [])

    def test_SEC_D_same_secret_store(self) -> None:
        from multi_agent_room import secret_store as ss_mod
        from multi_agent_room.model_service import ModelService as MS

        a = get_secret_store()
        b = MS().secrets
        self.assertIs(a, b)
        self.assertIsInstance(a, SecretStore)
        self.assertIs(ss_mod.get_secret_store(), a)

    def test_unknown_provider_falls_back_to_openai_compat(self) -> None:
        """OpenAI 兼容口：未知厂商别名仍可添加，协议走 openai_compat。"""
        from multi_agent_room.adapters import get_adapter, resolve_provider_id

        self.assertEqual(resolve_provider_id("deepseek"), "openai_compat")
        self.assertEqual(resolve_provider_id("not-exist-vendor"), "openai_compat")
        cfg = self.svc.add_model(
            display_name="DeepSeek",
            base_url="https://api.deepseek.com",
            model_id="deepseek-chat",
            api_key="sk-test",
            provider_id="deepseek",
        )
        self.assertEqual(cfg.provider_id, "deepseek")
        self.assertEqual(get_adapter(cfg.provider_id).id, "openai_compat")
        self.assertEqual(cfg.base_url, "https://api.deepseek.com/v1")

    def test_normalize_and_pick_model(self) -> None:
        from multi_agent_room.adapters import (
            guess_display_name,
            normalize_openai_base,
            pick_preferred_model,
        )

        self.assertEqual(
            normalize_openai_base("https://api.deepseek.com"),
            "https://api.deepseek.com/v1",
        )
        self.assertEqual(
            normalize_openai_base("https://api.deepseek.com/v1/"),
            "https://api.deepseek.com/v1",
        )
        self.assertEqual(
            pick_preferred_model(["deepseek-reasoner", "deepseek-chat"]),
            "deepseek-chat",
        )
        self.assertEqual(guess_display_name("https://api.deepseek.com"), "DeepSeek")

    def test_add_from_endpoint_auto_model_and_probe(self) -> None:
        from multi_agent_room.adapters import ListModelsResult, ProbeResult

        def fake_list(self_ad, cfg, key):  # noqa: ANN001
            return ListModelsResult(
                True,
                "ok",
                "ok",
                model_ids=["Deepseek-v4-pro", "deepseek-chat"],
                ui_text="ok",
            )

        def fake_probe(self_ad, cfg, key):  # noqa: ANN001
            return ProbeResult(
                True, "ok", "ok", "探活成功", suggested_model_id=cfg.model_id
            )

        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.list_models", fake_list
        ), patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe", fake_probe
        ):
            cfg, result, listed = self.svc.add_from_endpoint(
                base_url="https://api.deepseek.com",
                api_key="sk-x",
            )
        self.assertTrue(listed.ok)
        self.assertEqual(cfg.model_id, "deepseek-chat")
        self.assertEqual(cfg.base_url, "https://api.deepseek.com/v1")
        self.assertTrue(result and result.ok)
        self.assertEqual(cfg.status, "ready")


if __name__ == "__main__":
    unittest.main()
