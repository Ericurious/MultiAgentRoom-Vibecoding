"""T-M8a 验收：M8a-A～M8a-D。"""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_room.adapters import ProbeResult  # noqa: E402
from multi_agent_room.agent_service import AgentService  # noqa: E402
from multi_agent_room.budget import BudgetConfig  # noqa: E402
from multi_agent_room.logging_setup import LOGGER_NAME, setup_logging  # noqa: E402
from multi_agent_room.model_service import ModelService  # noqa: E402
from multi_agent_room.orchestrator import Orchestrator  # noqa: E402
from multi_agent_room.phase_machine import (  # noqa: E402
    DEFAULT_AGENDA,
    agenda_contains_review_judge_confirm,
    can_transition,
)
from multi_agent_room.room_service import RoomService  # noqa: E402


class M8aTests(unittest.TestCase):
    def setUp(self) -> None:
        setup_logging()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._env = patch.dict("os.environ", {"APPDATA": self._tmpdir.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        import multi_agent_room.secret_store as ss

        ss._STORE = None

        self.models = ModelService()
        self.agents = AgentService(models=self.models)
        self.orch = Orchestrator(
            budget_config=BudgetConfig(
                max_patches_per_round=3,
                max_r2=2,
                max_confirm_churn=2,
            )
        )
        self.svc = RoomService(
            agents=self.agents, models=self.models, orchestrator=self.orch
        )

        self.cfg = self.models.add_model(
            display_name="ModelA",
            base_url="https://a.example/v1",
            model_id="a",
            api_key="sk-a",
        )
        with patch(
            "multi_agent_room.adapters.OpenAICompatAdapter.probe",
            lambda *a, **k: ProbeResult(True, "ok", "ok", "探活成功"),
        ):
            self.models.probe(self.cfg.config_id)
        self.agent = self.agents.create_agent(
            display_name="Worker",
            model_config_id=self.cfg.config_id,
            agent_id="agent-m8a",
        )

    def _room_ready(self):
        room = self.svc.create_room("M8a房")
        self.svc.invite_ready_agent(room.room_id, self.agent.agent_id)
        return self.svc.get_room(room.room_id)

    def test_M8a_A_default_agenda(self) -> None:
        self.assertTrue(agenda_contains_review_judge_confirm(DEFAULT_AGENDA))
        room = self._room_ready()
        assert room is not None
        self.assertTrue(agenda_contains_review_judge_confirm(room.agenda))
        text = room.agenda_text()
        self.assertIn("ReviewOpen", text)
        self.assertIn("AwaitingJudge", text)
        self.assertIn("ConfirmOpen", text)
        # 顺序：审阅→评判→确认
        i_r = room.agenda.index("ReviewOpen")
        i_j = room.agenda.index("AwaitingJudge")
        i_c = room.agenda.index("ConfirmOpen")
        self.assertLess(i_r, i_j)
        self.assertLess(i_j, i_c)

    def test_M8a_B_illegal_transition_logged(self) -> None:
        room = self._room_ready()
        assert room is not None
        self.assertFalse(can_transition("Idle", "Final"))
        logger = logging.getLogger(LOGGER_NAME)
        with self.assertLogs(logger, level="INFO") as cm:
            ok, msg = self.orch.transition(room.room_id, "Final", reason="非法试探")
        self.assertFalse(ok)
        self.assertIn("非法", msg)
        self.assertEqual(room.phase, "Idle")  # 未改
        joined = "\n".join(cm.output)
        self.assertIn("phase_reject", joined)
        self.assertTrue(
            "protocol_reject" in joined or "illegal_phase" in joined,
            msg=joined,
        )

    def test_M8a_C_frozen_same_field(self) -> None:
        room = self._room_ready()
        assert room is not None
        self.svc.ask_question(room.room_id, "打断同源")
        self.agents.user_assign_roles(
            room.room_id,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(room.room_id, self.agent.agent_id, "ans")
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertTrue(self.orch.frozen_shares_room_object(room))

        self.svc.interrupt(room.room_id)
        room2 = self.svc.get_room(room.room_id)
        orch_room = self.orch.require(room.room_id).room
        assert room2 is not None
        self.assertIs(room2, orch_room)
        self.assertTrue(room2.frozen)
        self.assertTrue(orch_room.frozen)
        self.assertEqual(room2.phase, "Frozen")
        self.assertEqual(orch_room.phase, room2.phase)

        self.svc.resume(room.room_id)
        self.assertFalse(orch_room.frozen)
        self.assertEqual(orch_room.phase, "ReviewOpen")

    def test_M8a_D_patch_budget_stops(self) -> None:
        room = self._room_ready()
        assert room is not None
        self.svc.ask_question(room.room_id, "预算")
        self.agents.user_assign_roles(
            room.room_id,
            first_answerer_agent_id=self.agent.agent_id,
            judge_is_user=True,
        )
        self.svc.submit_first_answer(room.room_id, self.agent.agent_id, "ans")
        # max_patches=3 → 第 4 次失败并升用户
        hints = []
        for i in range(3):
            ok, msg = self.svc.record_patch(room.room_id)
            self.assertTrue(ok, msg=f"i={i} {msg}")
        ok, msg = self.svc.record_patch(room.room_id)
        self.assertFalse(ok)
        self.assertIn("超补丁预算", msg)
        room = self.svc.get_room(room.room_id)
        assert room is not None
        self.assertEqual(room.phase, "AwaitingUserEscalation")
        self.assertTrue(self.orch.require(room.room_id).budget.stopped)
        self.assertIn("超补丁", room.escalation_hint or msg)


if __name__ == "__main__":
    unittest.main()
