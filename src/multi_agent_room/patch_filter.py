"""T-M5：补丁过滤器（字段/全文重写/琐碎）+ 待合入队列。"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.net_change import detect_kind, net_change, similarity

# 实质类别（TRIV-5：other/空 不算「已声明实质」）
HARD_SUBSTANTIVE = frozenset(
    {"scheme", "api", "logic", "fact", "acceptance", "fatal", "missing", "security"}
)
ALL_CATEGORIES = HARD_SUBSTANTIVE | {"other"}

LOW_VALUE_CLAIM = re.compile(
    r"^(优化一下|润色|改改措辞|稍微改改|调整一下语气|polish|tweak wording)\s*$",
    re.I,
)
ADJ_ONLY = re.compile(
    r"^(很好|非常好|不错|挺好|更好|更佳|excellent|great|nice)[.!。！]?$",
    re.I,
)
PUNCT_HEAVY = re.compile(r"^[\s\w\u4e00-\u9fff]*$")  # used with small net + same meaning heuristics

SIX_DIM_HINT = (
    "评议六维（系统提示）：题意对齐 / 正确性 / 完整性 / 可行性 / 风险 / 一致性；"
    "不含文采偏好。"
)


@dataclass
class PatchItem:
    patch_id: str
    room_id: str
    agent_id: str
    target: str
    category: str
    claim: str
    replace: str
    version: int
    ts: float = field(default_factory=time.time)
    marked_trivial: bool = False

    @property
    def fields(self) -> dict[str, Any]:
        """兼容 PROTO 时代 `pending_patches()[i].fields` 访问。"""
        return {
            "patch_id": self.patch_id,
            "target": self.target,
            "category": self.category,
            "claim": self.claim,
            "replace": self.replace,
            "version": self.version,
            "agent_id": self.agent_id,
        }


@dataclass
class FilterResult:
    ok: bool
    code: str  # ok | invalid | multi_target | full_rewrite | trivial | out_of_scope
    message: str
    patch: Optional[PatchItem] = None


class PatchFilter:
    def __init__(self) -> None:
        self.rewrite_tokens: dict[str, int] = {}  # room_id -> remaining

    def grant_rewrite(self, room_id: str, n: int = 1) -> None:
        self.rewrite_tokens[room_id] = self.rewrite_tokens.get(room_id, 0) + n
        log_event("rewrite_token", f"grant={n}", room_id=room_id)

    def filter(
        self,
        *,
        room_id: str,
        agent_id: str,
        fields: dict[str, Any],
        old_text: str,
        doc_full: str,
        doc_version: int,
        other_block_texts: Optional[dict[str, str]] = None,
        allowed_targets: Optional[set[str]] = None,
        skip_rewrite_check: bool = False,
    ) -> FilterResult:
        target = str(fields.get("target") or "").strip()
        category = str(fields.get("category") or "").strip().lower()
        claim = str(fields.get("claim") or "").strip()
        replace = fields.get("replace")
        if replace is None:
            replace = ""
        replace = str(replace)

        # 1) 缺字段
        missing = [k for k, v in [("target", target), ("category", category), ("claim", claim)] if not v]
        if "replace" not in fields and replace == "":
            # empty replace allowed as delete — still need key present; PROTO already requires
            pass
        if missing:
            return FilterResult(False, "invalid", f"缺字段: {', '.join(missing)}")

        # 2) multi-target
        if isinstance(fields.get("target"), list) or "," in target:
            return FilterResult(False, "multi_target", "禁止 multi-target PATCH")

        # 超范围
        if allowed_targets is not None and target not in allowed_targets:
            return FilterResult(
                False, "out_of_scope", f"超范围拒收: {target} 不在 ChangedSet"
            )

        # 3) 全文重写
        use_token = False
        if not skip_rewrite_check:
            rw = self._check_rewrite(
                room_id, target, old_text, replace, doc_full, other_block_texts or {}
            )
            if rw:
                if self.rewrite_tokens.get(room_id, 0) > 0:
                    self.rewrite_tokens[room_id] -= 1
                    use_token = True
                    log_event("rewrite_token", "consumed", room_id=room_id)
                else:
                    return FilterResult(False, "full_rewrite", rw)

        # 4) 琐碎
        triv = self._check_trivial(old_text, replace, category, claim)
        if triv:
            return FilterResult(False, "trivial", triv)

        if category and category not in ALL_CATEGORIES:
            # 未知类别：仍可入队（交评判），但不算硬实质
            pass

        item = PatchItem(
            patch_id=f"patch-{uuid.uuid4().hex[:10]}",
            room_id=room_id,
            agent_id=agent_id,
            target=target,
            category=category,
            claim=claim,
            replace=replace,
            version=doc_version,
        )
        msg = "ok"
        if use_token:
            msg = "ok(rewrite_token)"
        return FilterResult(True, "ok", msg, patch=item)

    def _check_rewrite(
        self,
        room_id: str,
        target: str,
        old: str,
        replace: str,
        doc_full: str,
        others: dict[str, str],
    ) -> str:
        # RW-3: other blockIds or ≥2 h1
        if re.search(r"\bB\d{2,}\b", replace) and target not in re.findall(
            r"\bB\d{2,}\b", replace
        ):
            # has block ids that aren't only self references in prose — if any other Bxx
            ids = set(re.findall(r"\bB\d{2,}\b", replace))
            if ids - {target}:
                return "RW-3: replace 含其它 blockId"
        if len(re.findall(r"(?m)^#\s+\S", replace)) >= 2:
            return "RW-3: 整章标题重建（≥2 一级标题）"

        # RW-2：全文足够长时才用 80% 长度闸；或含 ≥3 个非 target 块片段
        if doc_full and len(doc_full) >= 120 and len(replace) > 0.8 * len(doc_full):
            return "RW-2: replace 超过全文 80%"
        hit = 0
        for bid, txt in others.items():
            if bid == target or not txt.strip():
                continue
            frag = txt.strip()[:40]
            if len(frag) >= 12 and frag in replace:
                hit += 1
        if hit >= 3:
            return "RW-2: 包含 ≥3 个非 target 块原文片段"

        # RW-1（短块局部修补如 try/except 不误杀；长块按相似度）
        if old.strip() and len(old) >= 48:
            sim = similarity(old, replace)
            n = net_change(old, replace)
            if sim < 0.30 and n > 0.7 * max(len(old), 1):
                return "RW-1: 相似度过低且净变更过大"
        return ""

    def _check_trivial(
        self, old: str, replace: str, category: str, claim: str
    ) -> str:
        # SUB-4 例外：硬实质类别 + 致命/错误 claim → 不过 TRIV
        fatalish = bool(
            re.search(
                r"崩溃|致命|错误处理|事实|不符|签名|控制流|Playwright|try/except|遗漏",
                claim,
            )
        )
        if category in HARD_SUBSTANTIVE and fatalish:
            return ""

        # TRIV-1 纯形容词
        if ("很好" in old and "非常好" in replace) or (
            ADJ_ONLY.match(old.strip() or "")
            and ADJ_ONLY.match(replace.strip() or "")
            and net_change(old, replace) < 20
        ):
            return "TRIV-1: 纯形容词"

        # TRIV-2 标点/错别字润色且不改结论
        old_alnum = re.sub(r"[\W_]+", "", old, flags=re.UNICODE)
        new_alnum = re.sub(r"[\W_]+", "", replace, flags=re.UNICODE)
        if old and old_alnum == new_alnum and old != replace:
            if category not in HARD_SUBSTANTIVE:
                return "TRIV-2: 标点/润色不改结论"

        # TRIV-3 不重要步骤建议
        if re.search(r"建议保存后再运行|可选地|顺便", replace):
            added_only = replace.startswith(old) or old in replace
            if added_only and category not in HARD_SUBSTANTIVE and not fatalish:
                return "TRIV-3: 不重要步骤"

        # TRIV-4 删除重复句/步骤
        if old and replace is not None and category not in HARD_SUBSTANTIVE:
            ol = [ln for ln in old.splitlines() if ln.strip()]
            nl = [ln for ln in replace.splitlines() if ln.strip()]
            if len(ol) > len(nl) and set(nl).issubset(set(ol)):
                from collections import Counter

                if any(v >= 2 for v in Counter(ol).values()) or len(ol) - len(nl) >= 1:
                    if net_change(old, replace) < 80:
                        return "TRIV-4: 删除重复步骤"

        # TRIV-5：净变更<100 且无硬实质类别 且 claim 低价值
        n = net_change(old, replace)
        if (
            n < 100
            and category not in HARD_SUBSTANTIVE
            and LOW_VALUE_CLAIM.search(claim)
        ):
            return "TRIV-5: 低价值短改"

        return ""


class PatchQueue:
    def __init__(self) -> None:
        self._q: dict[str, list[PatchItem]] = {}

    def enqueue(self, item: PatchItem) -> None:
        self._q.setdefault(item.room_id, []).append(item)

    def list(self, room_id: str) -> list[PatchItem]:
        return list(self._q.get(room_id) or [])

    def clear(self, room_id: str) -> list[PatchItem]:
        return self._q.pop(room_id, [])

    def get(self, room_id: str, patch_id: str) -> Optional[PatchItem]:
        for p in self._q.get(room_id) or []:
            if p.patch_id == patch_id:
                return p
        return None

    def mark_trivial(self, room_id: str, patch_id: str) -> bool:
        p = self.get(room_id, patch_id)
        if not p:
            return False
        p.marked_trivial = True
        return self.dequeue(room_id, patch_id)

    def dequeue(self, room_id: str, patch_id: str) -> bool:
        q = self._q.get(room_id) or []
        n = len(q)
        self._q[room_id] = [x for x in q if x.patch_id != patch_id]
        return len(self._q[room_id]) < n
