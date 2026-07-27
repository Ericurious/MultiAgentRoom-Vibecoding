"""T-ENV 验收测试（无 GUI）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 保证可导入 src 包
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_room.config import AppConfig, load_config, save_config  # noqa: E402
from multi_agent_room.logging_setup import (  # noqa: E402
    latest_log_file,
    log_event,
    log_judge_approve,
    log_merge_accept,
    log_phase_change,
    log_probe_result,
    log_protocol_reject,
    log_reject_verdict,
    setup_logging,
)
from multi_agent_room.paths import (  # noqa: E402
    ensure_workspace_dir,
    get_appdata_root,
    get_config_dir,
    get_data_dir,
    get_logs_dir,
    normalize_workspace_path,
)


class EnvPathsTests(unittest.TestCase):
    def test_appdata_dirs_created(self) -> None:
        for d in (get_appdata_root(), get_config_dir(), get_data_dir(), get_logs_dir()):
            self.assertTrue(d.is_dir(), msg=str(d))

    def test_drive_letter_workspace(self) -> None:
        # 使用临时目录模拟盘符下路径
        with tempfile.TemporaryDirectory() as tmp:
            p = ensure_workspace_dir(tmp)
            self.assertTrue(p.is_dir())
            self.assertTrue(p.is_absolute())
            # Windows 盘符形态
            if os.name == "nt":
                self.assertRegex(str(p), r"^[A-Za-z]:\\")


class EnvConfigTests(unittest.TestCase):
    def test_save_reload_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config()
            cfg.set_workspace(tmp)
            cfg.bind_room_workspace("room-a", tmp)
            path = save_config(cfg)
            self.assertTrue(path.is_file())

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                normalize_workspace_path(raw["workspace_path"]),
                normalize_workspace_path(tmp),
            )
            self.assertEqual(raw["rooms"][0]["room_id"], "room-a")

            again = load_config()
            self.assertIsNotNone(again.workspace_path)
            self.assertEqual(
                normalize_workspace_path(again.workspace_path or ""),
                normalize_workspace_path(tmp),
            )


class EnvLoggingTests(unittest.TestCase):
    def test_min_events_and_chinese_and_redact(self) -> None:
        setup_logging()
        log_phase_change("Idle", "Campaign", room_id="r1")
        log_probe_result("c1", "auth", "鉴权失败")
        log_protocol_reject("缺 claim")
        log_merge_accept("p1", "B01")
        log_reject_verdict("R2", "方向错误")
        log_judge_approve("r1")
        log_event("general", "api_key=sk-live-secret-value 不应明文")

        lf = latest_log_file()
        self.assertIsNotNone(lf)
        assert lf is not None
        text = lf.read_text(encoding="utf-8")
        self.assertIn("phase_change", text)
        self.assertIn("probe_result", text)
        self.assertIn("protocol_reject", text)
        self.assertIn("merge_accept", text)
        self.assertIn("verdict_reject", text)
        self.assertIn("judge_approve", text)
        self.assertIn("鉴权失败", text)
        self.assertNotIn("sk-live-secret-value", text)
        self.assertIn("***", text)


if __name__ == "__main__":
    unittest.main()
