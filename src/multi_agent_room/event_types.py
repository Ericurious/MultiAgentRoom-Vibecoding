"""T-BUS-02：房间事件总线事件类型全集（对齐 spec §5.9）。"""

from __future__ import annotations

from typing import Final, FrozenSet

# 权威 P0 事件类型（白板广播；非点对点私信）
BUS_EVENT_TYPES: Final[FrozenSet[str]] = frozenset(
    {
        "DocVersion",
        "Read",
        "PatchAccepted",
        "PatchRejected",
        "QueueUpdated",
        "Verdict",  # payload.verdict ∈ R1|R2|R3|Accept|Merge
        "JudgeApprove",
        "FinalCommitted",
        "ToolReceipt",
        "Clarify",
        "SilentCheckPass",
        "RoomIdle",
        "RoomAwake",
    }
)

VERDICT_KINDS: Final[FrozenSet[str]] = frozenset(
    {"R1", "R2", "R3", "Accept", "Merge"}
)

# 合入后验收必出现的配对
MERGE_PAIR: Final[tuple[str, str]] = ("DocVersion", "QueueUpdated")
