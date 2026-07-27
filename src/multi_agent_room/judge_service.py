"""T-M6：评判服务 — 合入 / 冲突合并 / R1·R2·R3 / JudgeApprove 门禁。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from multi_agent_room.conflict import (
    ConflictReport,
    classify_target_conflict,
    stack_replaces,
)
from multi_agent_room.logging_setup import log_event
from multi_agent_room.net_change import net_change
from multi_agent_room.patch_filter import PatchItem
from multi_agent_room.prompts import ROOM_RULES_SUMMARY, SIX_DIM_REVIEW_HINT

MergeMode = Literal["stack", "choose", "rewrite", "concat"]

R2_REQUIRED = (
    "user_goal_ref",
    "wrong_direction",
    "required_direction",
)
R2_POLISH = (
    "润色",
    "改改措辞",
    "文采",
    "polish",
    "tweak wording",
    "优化措辞",
)
R3_FORBIDDEN = (
    "换算法",
    "换架构",
    "改架构",
    "换库",
    "Playwright",
    "API 契约",
    "对外 API",
    "验收标准",
    "改方案方向",
)


@dataclass
class MergeRecord:
    record_id: str
    room_id: str
    target: str
    mode: MergeMode
    reason: str
    candidate_patch_ids: list[str]
    final_text: str
    strategy: str = ""
    ts: float = field(default_factory=time.time)


@dataclass
class OpenReject:
    reject_id: str
    room_id: str
    target: str
    reason: str
    assignee_agent_id: str
    closed: bool = False
    ts: float = field(default_factory=time.time)


@dataclass
class JudgeContext:
    user_anchor: str
    shared_doc: str
    pending_patches: list[dict[str, Any]]
    open_rejects: list[dict[str, Any]]
    rules_summary: str
    six_dim: str = SIX_DIM_REVIEW_HINT

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_anchor": self.user_anchor,
            "shared_doc": self.shared_doc,
            "pending_patches": self.pending_patches,
            "open_rejects": self.open_rejects,
            "rules_summary": self.rules_summary,
            "six_dim": self.six_dim,
        }

    def contains_chatter(self, phrases: list[str]) -> bool:
        blob = (
            self.user_anchor
            + self.shared_doc
            + self.rules_summary
            + str(self.pending_patches)
            + str(self.open_rejects)
        )
        return any(p and p in blob for p in phrases)


@dataclass
class JudgeResult:
    ok: bool
    message: str
    changed_set: list[str] = field(default_factory=list)
    version: Optional[int] = None
    merge_record: Optional[MergeRecord] = None
    opened_confirm: bool = False


class JudgeService:
    def __init__(
        self,
        *,
        r3_max_blocks: int = 3,
        r3_max_diff_lines: int = 40,
        r1_max_per_block: int = 2,
    ) -> None:
        self.r3_max_blocks = r3_max_blocks
        self.r3_max_diff_lines = r3_max_diff_lines
        self.r1_max_per_block = r1_max_per_block
        self.merge_records: dict[str, list[MergeRecord]] = {}
        self.open_rejects: dict[str, list[OpenReject]] = {}
        self.r1_streak: dict[str, dict[str, int]] = {}  # room -> target -> count
        self.changed_sets: dict[str, set[str]] = {}
        self._block_authors: dict[str, dict[str, str]] = {}

    def note_block_author(self, room_id: str, block_id: str, agent_id: str) -> None:
        self._block_authors.setdefault(room_id, {})[block_id] = agent_id

    def build_context(
        self,
        *,
        question: str,
        shared_doc: str,
        pending: list[PatchItem],
        room_rules: str = ROOM_RULES_SUMMARY,
    ) -> JudgeContext:
        rejects = []
        for room_rejects in self.open_rejects.values():
            for r in room_rejects:
                if not r.closed and r.room_id:
                    pass
        # caller passes room-scoped via list_open_rejects
        return JudgeContext(
            user_anchor=question.strip(),
            shared_doc=shared_doc,
            pending_patches=[p.fields for p in pending],
            open_rejects=[],  # filled by facade
            rules_summary=room_rules,
        )

    def list_open_rejects(self, room_id: str) -> list[OpenReject]:
        return [r for r in self.open_rejects.get(room_id, []) if not r.closed]

    def has_open_rejects(self, room_id: str) -> bool:
        return bool(self.list_open_rejects(room_id))

    def get_changed_set(self, room_id: str) -> list[str]:
        return sorted(self.changed_sets.get(room_id) or [])

    def list_merge_records(self, room_id: str) -> list[MergeRecord]:
        return list(self.merge_records.get(room_id) or [])

    def conflicts_for(self, room_id: str, pending: list[PatchItem]) -> list[ConflictReport]:
        by_t: dict[str, list[PatchItem]] = {}
        for p in pending:
            by_t.setdefault(p.target, []).append(p)
        out = []
        for t, ps in by_t.items():
            if len(ps) >= 2:
                out.append(classify_target_conflict(ps))
        return out

    def validate_r2(self, payload: dict[str, Any]) -> tuple[bool, str]:
        missing = [k for k in R2_REQUIRED if not str(payload.get(k) or "").strip()]
        # keep/discard 至少一个
        keep = payload.get("keep")
        discard = payload.get("discard")
        if keep is None and discard is None:
            missing.append("keep|discard")
        if missing:
            return False, f"R2 缺字段: {', '.join(missing)}"
        reason_blob = " ".join(
            str(payload.get(k) or "")
            for k in ("wrong_direction", "required_direction", "reason")
        )
        if any(p in reason_blob for p in R2_POLISH):
            return False, "R2 拒收润色理由"
        return True, "ok"

    def validate_r3(
        self,
        *,
        targets: list[str],
        old_by_target: dict[str, str],
        new_by_target: dict[str, str],
        claim: str = "",
    ) -> tuple[bool, str]:
        if not targets:
            return False, "R3 缺 target"
        if len(targets) > self.r3_max_blocks:
            return False, f"R3 越界: 区块数>{self.r3_max_blocks}"
        blob = claim + " ".join(new_by_target.values())
        if any(f in blob for f in R3_FORBIDDEN):
            return False, "R3 越界: 属于方案/架构变更，须 R2 或回补丁"
        total_lines = 0
        for t in targets:
            old, new = old_by_target.get(t, ""), new_by_target.get(t, "")
            total_lines += _diff_line_count(old, new)
        if total_lines > self.r3_max_diff_lines:
            return False, f"R3 越界: diff 行>{self.r3_max_diff_lines}"
        return True, "ok"

    def open_r1(
        self,
        room_id: str,
        *,
        target: str,
        reason: str,
        assignee_agent_id: str = "",
        first_answerer: str = "",
    ) -> tuple[bool, str, Optional[OpenReject]]:
        streak = self.r1_streak.setdefault(room_id, {})
        n = streak.get(target, 0) + 1
        if n > self.r1_max_per_block:
            return False, f"同块 R1 超限({self.r1_max_per_block})，升用户", None
        streak[target] = n
        author = (
            assignee_agent_id
            or self._block_authors.get(room_id, {}).get(target)
            or first_answerer
        )
        rej = OpenReject(
            reject_id=f"r1-{uuid.uuid4().hex[:10]}",
            room_id=room_id,
            target=target,
            reason=reason or "局部打回",
            assignee_agent_id=author,
        )
        self.open_rejects.setdefault(room_id, []).append(rej)
        log_event("r1_open", f"target={target} assignee={author}", room_id=room_id)
        return True, "ok", rej

    def close_r1_for_target(self, room_id: str, target: str) -> list[OpenReject]:
        closed = []
        for r in self.open_rejects.get(room_id, []):
            if not r.closed and r.target == target:
                r.closed = True
                closed.append(r)
                log_event("r1_close", f"target={target}", room_id=room_id)
        if closed:
            self.r1_streak.setdefault(room_id, {})[target] = 0
        return closed

    def record_merge(self, rec: MergeRecord) -> None:
        self.merge_records.setdefault(rec.room_id, []).append(rec)

    def set_changed_set(self, room_id: str, blocks: list[str]) -> None:
        self.changed_sets[room_id] = set(blocks)

    def clear_confirm_state(self, room_id: str) -> None:
        self.changed_sets.pop(room_id, None)


def _diff_line_count(old: str, new: str) -> int:
    from collections import Counter

    oc = Counter([ln for ln in (old or "").splitlines() if ln.strip()])
    nc = Counter([ln for ln in (new or "").splitlines() if ln.strip()])
    return int(sum((oc - nc).values()) + sum((nc - oc).values()))


def make_merge_record(
    *,
    room_id: str,
    target: str,
    mode: MergeMode,
    reason: str,
    patch_ids: list[str],
    final_text: str,
    strategy: str = "",
) -> MergeRecord:
    return MergeRecord(
        record_id=f"merge-{uuid.uuid4().hex[:10]}",
        room_id=room_id,
        target=target,
        mode=mode,
        reason=reason,
        candidate_patch_ids=list(patch_ids),
        final_text=final_text,
        strategy=strategy,
    )
