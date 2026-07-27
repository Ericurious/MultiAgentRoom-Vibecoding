"""M8a：phase 合法迁移表（T-M8a-01）。"""

from __future__ import annotations

from typing import FrozenSet

# 权威 phase 集合（对齐 spec M8 全表）
ALL_PHASES: FrozenSet[str] = frozenset(
    {
        "Idle",
        "Campaign",
        "AwaitingFirstAnswer",
        "ReviewOpen",
        "AwaitingJudge",
        "ConfirmOpen",
        "AwaitingUserEscalation",
        "Final",
        "Frozen",
        "AwaitingUserClarify",
    }
)

# 可迁出至（不含 Frozen/Clarify 的「恢复」特例，由 Orchestrator 单独处理）
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "Idle": frozenset({"Campaign"}),
    "Campaign": frozenset({"AwaitingFirstAnswer", "Idle"}),
    "AwaitingFirstAnswer": frozenset({"ReviewOpen", "Idle"}),
    "ReviewOpen": frozenset(
        {"AwaitingJudge", "Frozen", "AwaitingUserClarify", "AwaitingFirstAnswer"}
    ),
    "AwaitingJudge": frozenset(
        {
            "ConfirmOpen",
            "Final",
            "AwaitingFirstAnswer",  # R2
            "Frozen",
            "AwaitingUserClarify",
            "ReviewOpen",  # 合入后新版本再开审阅（预留）
        }
    ),
    "ConfirmOpen": frozenset(
        {
            "AwaitingJudge",
            "ConfirmOpen",  # 再 +1
            "AwaitingUserEscalation",
            "Frozen",
            "AwaitingUserClarify",
            "Final",
        }
    ),
    "AwaitingUserEscalation": frozenset(
        {"Final", "AwaitingFirstAnswer", "ConfirmOpen", "Idle"}
    ),
    "Final": frozenset({"Idle", "Final"}),
    "Frozen": frozenset(),  # 仅 Resume 特例
    "AwaitingUserClarify": frozenset(),  # 仅清除 Hold 特例
}

# 允许进入 Frozen 的 phase
FREEZABLE: FrozenSet[str] = frozenset(
    {"ReviewOpen", "ConfirmOpen", "AwaitingJudge"}
)

# 允许进入澄清 Hold 的 phase
CLARIFIABLE: FrozenSet[str] = frozenset(
    {"ReviewOpen", "ConfirmOpen", "AwaitingJudge"}
)

# 新房默认议程（须含审阅→评判→确认）
DEFAULT_AGENDA: tuple[str, ...] = (
    "Campaign",
    "AwaitingFirstAnswer",
    "ReviewOpen",  # 审阅
    "AwaitingJudge",  # 评判
    "ConfirmOpen",  # 确认
    "Final",
)


def can_transition(from_phase: str, to_phase: str) -> bool:
    if from_phase not in ALL_PHASES or to_phase not in ALL_PHASES:
        return False
    allowed = LEGAL_TRANSITIONS.get(from_phase, frozenset())
    return to_phase in allowed


def agenda_contains_review_judge_confirm(agenda: list[str] | tuple[str, ...]) -> bool:
    """M8a-A：默认议程含审阅→评判→确认（顺序）。"""
    need = ["ReviewOpen", "AwaitingJudge", "ConfirmOpen"]
    try:
        idxs = [list(agenda).index(x) for x in need]
    except ValueError:
        return False
    return idxs[0] < idxs[1] < idxs[2]
