"""T-M6：同块冲突分类（spec §6.3）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from multi_agent_room.patch_filter import PatchItem

ConflictKind = Literal["compatible_stack", "incompatible", "pending"]


@dataclass
class ConflictReport:
    target: str
    kind: ConflictKind
    patches: list[PatchItem]
    reason: str = ""


_DIFF_HUNK = re.compile(r"(?m)^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def _looks_like_diff(text: str) -> bool:
    t = text or ""
    return bool(_DIFF_HUNK.search(t) or t.lstrip().startswith("@@") or t.lstrip().startswith("---"))


def _diff_ranges(text: str) -> Optional[list[tuple[int, int]]]:
    """解析 unified diff 行区间；失败返回 None（→ 待裁定）。"""
    if not _looks_like_diff(text):
        return None
    ranges: list[tuple[int, int]] = []
    for m in _DIFF_HUNK.finditer(text):
        start = int(m.group(1))
        count = int(m.group(2) or "1")
        ranges.append((start, start + max(count, 1) - 1))
    return ranges or None


def _ranges_overlap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    for s1, e1 in a:
        for s2, e2 in b:
            if s1 <= e2 and s2 <= e1:
                return True
    return False


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def classify_target_conflict(patches: list[PatchItem]) -> ConflictReport:
    if not patches:
        raise ValueError("无补丁")
    target = patches[0].target
    if len(patches) < 2:
        return ConflictReport(target, "compatible_stack", patches, "单补丁无需合并")

    # 均为整块 replace 且归一化不等 → 不兼容
    diffs = [_looks_like_diff(p.replace) for p in patches]
    if all(not d for d in diffs):
        norms = {_norm(p.replace) for p in patches}
        if len(norms) > 1:
            return ConflictReport(
                target, "incompatible", patches, "整块 replace 文本不等"
            )
        return ConflictReport(target, "compatible_stack", patches, "replace 等价可叠")

    if all(diffs):
        ranges_list = [_diff_ranges(p.replace) for p in patches]
        if any(r is None for r in ranges_list):
            return ConflictReport(target, "pending", patches, "diff 解析失败")
        # 任两区间相交 → 不兼容
        for i in range(len(ranges_list)):
            for j in range(i + 1, len(ranges_list)):
                if _ranges_overlap(ranges_list[i] or [], ranges_list[j] or []):
                    return ConflictReport(
                        target, "incompatible", patches, "diff 行区间相交"
                    )
        return ConflictReport(
            target, "compatible_stack", patches, "diff 区间不重叠可叠"
        )

    # replace + diff 混用 → 待裁定/不兼容
    return ConflictReport(
        target, "incompatible", patches, "replace 与 diff 混用作用于同块"
    )


def stack_replaces(patches: list[PatchItem], *, order: Optional[list[str]] = None) -> str:
    """按序叠合：后者覆盖前者全文（P0 简化）；diff 则顺序拼接。"""
    items = list(patches)
    if order:
        by_id = {p.patch_id: p for p in items}
        items = [by_id[i] for i in order if i in by_id]
    if all(_looks_like_diff(p.replace) for p in items):
        return "\n".join(p.replace for p in items)
    # 全文 replace：取最后一贴（或 concat 文本）
    return items[-1].replace
