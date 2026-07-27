"""T-M4：共享稿数据模型、候选、已读、写入口。"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

from multi_agent_room.chunker import chunk_text
from multi_agent_room.logging_setup import log_event
from multi_agent_room.sec_guard import assert_no_secret_in_text

DocStatus = Literal["active", "voided", "candidate", "rejected"]
BaseFrom = Literal["firstAnswer", "merge", "tweak", "r1Fix"]


def new_doc_id() -> str:
    return f"doc-{uuid.uuid4().hex[:10]}"


def _block_id(n: int) -> str:
    return f"B{n:02d}"


@dataclass
class DocBlock:
    block_id: str
    text: str
    order: int
    block_type: str = "text"
    version: int = 1  # 块文本最近变更时的 doc version
    split_from: Optional[str] = None
    tombstoned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SharedDoc:
    doc_id: str
    room_id: str
    version: int = 0
    status: DocStatus = "candidate"
    blocks: list[DocBlock] = field(default_factory=list)
    tombstones: list[str] = field(default_factory=list)
    base_from: BaseFrom = "firstAnswer"
    author_agent_id: str = ""
    created_at: float = field(default_factory=time.time)
    voided_reason: str = ""

    def active_blocks(self) -> list[DocBlock]:
        return [b for b in self.blocks if not b.tombstoned]

    def get_block(self, block_id: str) -> Optional[DocBlock]:
        for b in self.blocks:
            if b.block_id == block_id:
                return b
        return None

    def render_lines(self) -> list[str]:
        if not self.active_blocks():
            return [f"(空稿 · DocVersion=V{self.version} status={self.status})"]
        lines = [f"DocVersion=V{self.version} status={self.status} id={self.doc_id}"]
        for b in sorted(self.active_blocks(), key=lambda x: x.order):
            lines.append(f"[{b.block_id} V{b.version}] {b.text}")
        return lines

    def full_text(self) -> str:
        return "\n\n".join(
            b.text for b in sorted(self.active_blocks(), key=lambda x: x.order)
        )


@dataclass
class ReadReceipt:
    agent_id: str
    version: int
    ts: float = field(default_factory=time.time)


@dataclass
class CandidateDoc:
    doc: SharedDoc
    selected: bool = False
    rejected: bool = False


class DocStore:
    """房间共享稿权威存储（唯一写入口经 DocService）。"""

    def __init__(self) -> None:
        self._active: dict[str, SharedDoc] = {}
        self._candidates: dict[str, list[CandidateDoc]] = {}
        self._reads: dict[str, list[ReadReceipt]] = {}
        self._history: dict[str, list[SharedDoc]] = {}

    def get_active(self, room_id: str) -> Optional[SharedDoc]:
        return self._active.get(room_id)

    def list_candidates(self, room_id: str) -> list[CandidateDoc]:
        return list(self._candidates.get(room_id) or [])

    def reads_for(self, room_id: str, version: Optional[int] = None) -> list[ReadReceipt]:
        items = list(self._reads.get(room_id) or [])
        if version is None:
            return items
        return [r for r in items if r.version == version]


class DocService:
    """M4 写入口：CreateFromFirstAnswer / 选定 / 合入替换 / R2 作废。"""

    def __init__(self, store: Optional[DocStore] = None) -> None:
        self.store = store or DocStore()

    # ---- T-M4-03 首答入库 ----
    def create_from_first_answer(
        self,
        room_id: str,
        text: str,
        *,
        agent_id: str = "",
        activate: bool = True,
    ) -> SharedDoc:
        assert_no_secret_in_text(text, where="共享稿")
        chunks = chunk_text(text)
        blocks = [
            DocBlock(
                block_id=_block_id(i + 1),
                text=c.text,
                order=i,
                block_type=c.block_type,
                version=1,
            )
            for i, c in enumerate(chunks)
        ]
        doc = SharedDoc(
            doc_id=new_doc_id(),
            room_id=room_id,
            version=1 if activate else 0,
            status="active" if activate else "candidate",
            blocks=blocks,
            base_from="firstAnswer",
            author_agent_id=agent_id,
        )
        if activate:
            # 新 active 前，旧 active 进历史（非 R2）
            old = self.store._active.get(room_id)
            if old and old.status == "active":
                self.store._history.setdefault(room_id, []).append(old)
            self.store._active[room_id] = doc
            self.store._reads[room_id] = []  # 新版本链：已读不继承
            log_event(
                "doc_create_first",
                f"doc={doc.doc_id} blocks={len(blocks)} V{doc.version}",
                room_id=room_id,
            )
        else:
            self.store._candidates.setdefault(room_id, []).append(
                CandidateDoc(doc=doc)
            )
            log_event(
                "doc_candidate",
                f"doc={doc.doc_id} blocks={len(blocks)}",
                room_id=room_id,
            )
        return doc

    # ---- T-M4-04 并行候选 ----
    def add_candidate(self, room_id: str, text: str, *, agent_id: str = "") -> SharedDoc:
        return self.create_from_first_answer(
            room_id, text, agent_id=agent_id, activate=False
        )

    def list_candidates(self, room_id: str) -> list[CandidateDoc]:
        return self.store.list_candidates(room_id)

    def select_candidate(
        self,
        room_id: str,
        doc_id: str,
        *,
        by: Literal["user", "judge", "first_valid"] = "user",
    ) -> SharedDoc:
        cands = self.store._candidates.get(room_id) or []
        chosen: Optional[CandidateDoc] = None
        for c in cands:
            if c.doc.doc_id == doc_id and not c.rejected:
                chosen = c
                break
        if not chosen:
            raise KeyError(f"候选不存在: {doc_id}")
        for c in cands:
            if c is chosen:
                c.selected = True
                c.doc.status = "active"
                c.doc.version = max(1, c.doc.version)
            else:
                c.rejected = True
                c.doc.status = "rejected"
        old = self.store._active.get(room_id)
        if old:
            self.store._history.setdefault(room_id, []).append(old)
        self.store._active[room_id] = chosen.doc
        self.store._reads[room_id] = []
        log_event(
            "doc_select",
            f"doc={doc_id} by={by} active=1 others_rejected",
            room_id=room_id,
        )
        return chosen.doc

    def select_first_valid(self, room_id: str) -> SharedDoc:
        cands = [
            c
            for c in (self.store._candidates.get(room_id) or [])
            if not c.rejected and c.doc.active_blocks()
        ]
        if not cands:
            raise ValueError("无可用候选")
        return self.select_candidate(
            room_id, cands[0].doc.doc_id, by="first_valid"
        )

    # ---- T-M4-05 版本 / 已读 ----
    def register_read(self, room_id: str, agent_id: str) -> ReadReceipt:
        doc = self.require_active(room_id)
        rc = ReadReceipt(agent_id=agent_id, version=doc.version)
        self.store._reads.setdefault(room_id, []).append(rc)
        return rc

    def reads_valid_for_current(self, room_id: str) -> list[ReadReceipt]:
        doc = self.require_active(room_id)
        return self.store.reads_for(room_id, version=doc.version)

    def bump_version(
        self,
        room_id: str,
        *,
        base_from: BaseFrom = "merge",
        clear_reads: bool = True,
    ) -> SharedDoc:
        doc = self.require_active(room_id)
        doc.version += 1
        doc.base_from = base_from
        if clear_reads:
            self.store._reads[room_id] = []
        log_event("doc_bump", f"V{doc.version} from={base_from}", room_id=room_id)
        return doc

    # ---- T-M4-02b blockId 稳定性 ----
    def apply_replace(
        self,
        room_id: str,
        block_id: str,
        new_text: str,
        *,
        bump: bool = True,
    ) -> SharedDoc:
        assert_no_secret_in_text(new_text, where="共享稿补丁")
        doc = self.require_active(room_id)
        if block_id in doc.tombstones:
            raise ValueError(f"tombstone 块不可 PATCH: {block_id}")
        blk = doc.get_block(block_id)
        if not blk or blk.tombstoned:
            raise KeyError(f"block 不存在: {block_id}")
        if new_text.strip() == "":
            blk.tombstoned = True
            if block_id not in doc.tombstones:
                doc.tombstones.append(block_id)
        else:
            blk.text = new_text
            if bump:
                blk.version = doc.version + 1
        if bump:
            self.bump_version(room_id, base_from="merge")
            if not blk.tombstoned:
                blk.version = doc.version
        return doc

    def split_block(
        self,
        room_id: str,
        block_id: str,
        parts: list[str],
    ) -> SharedDoc:
        """分裂：原 ID 保留主片段；新片段 splitFrom。"""
        doc = self.require_active(room_id)
        blk = doc.get_block(block_id)
        if not blk or blk.tombstoned:
            raise KeyError(block_id)
        if len(parts) < 2:
            raise ValueError("分裂至少两段")
        blk.text = parts[0]
        max_n = 0
        for b in doc.blocks:
            try:
                max_n = max(max_n, int(b.block_id.lstrip("B")))
            except ValueError:
                pass
        insert_at = doc.blocks.index(blk) + 1
        for i, p in enumerate(parts[1:], start=1):
            max_n += 1
            nb = DocBlock(
                block_id=_block_id(max_n),
                text=p,
                order=blk.order + i,
                block_type=blk.block_type,
                version=doc.version + 1,
                split_from=block_id,
            )
            doc.blocks.insert(insert_at, nb)
            insert_at += 1
        # 重排 order
        for i, b in enumerate(doc.blocks):
            b.order = i
        self.bump_version(room_id, base_from="merge")
        return doc

    def can_patch_target(self, room_id: str, target: Optional[str]) -> tuple[bool, str]:
        """供 M5 联调：无 target / 未知 / tombstone → 拒。"""
        if not target or not str(target).strip():
            return False, "缺 target：补丁拒收"
        doc = self.store.get_active(room_id)
        if not doc or doc.status != "active":
            return False, "无 active 共享稿"
        if target in doc.tombstones:
            return False, f"tombstone 不可 PATCH: {target}"
        blk = doc.get_block(target)
        if not blk or blk.tombstoned:
            return False, f"未知 blockId: {target}"
        return True, "ok"

    # ---- T-M4-06 R2 作废 ----
    def void_for_r2(self, room_id: str, *, reason: str = "R2") -> Optional[SharedDoc]:
        doc = self.store._active.get(room_id)
        if not doc:
            return None
        doc.status = "voided"
        doc.voided_reason = reason
        self.store._history.setdefault(room_id, []).append(doc)
        self.store._active.pop(room_id, None)
        self.store._reads[room_id] = []
        # 候选一并作废（旧轮）
        for c in self.store._candidates.get(room_id) or []:
            if c.doc.status == "candidate":
                c.doc.status = "voided"
                c.rejected = True
        log_event("doc_void_r2", f"doc={doc.doc_id} {reason}", room_id=room_id)
        return doc

    def requires_new_first_answer(self, room_id: str) -> bool:
        """R2 后无 active 稿则必须新首答才可再审。"""
        doc = self.store.get_active(room_id)
        return doc is None or doc.status != "active"

    def require_active(self, room_id: str) -> SharedDoc:
        doc = self.store.get_active(room_id)
        if not doc or doc.status != "active":
            raise ValueError("无 active 共享稿：须先首答入库（R2 后须新首答）")
        return doc


# ---- 兼容 M2 视图 ----
@dataclass
class SharedDocView:
    """UI/旧代码兼容视图；底层指向 DocService.active。"""

    room_id: str
    _svc: Optional[DocService] = None
    # 旧字段兼容
    doc_version: int = 0
    blocks: list[Any] = field(default_factory=list)

    def _sync_from_active(self) -> None:
        if not self._svc:
            return
        doc = self._svc.store.get_active(self.room_id)
        if not doc:
            self.doc_version = 0
            self.blocks = []
            return
        self.doc_version = doc.version
        self.blocks = [
            type("B", (), {"block_id": b.block_id, "version": b.version, "text": b.text})()
            for b in doc.active_blocks()
        ]

    def render_lines(self) -> list[str]:
        if self._svc:
            doc = self._svc.store.get_active(self.room_id)
            if doc:
                return doc.render_lines()
        self._sync_from_active()
        if not self.blocks:
            return [f"(空稿 · DocVersion=V{self.doc_version})"]
        lines = [f"DocVersion=V{self.doc_version}"]
        for b in self.blocks:
            lines.append(f"[{b.block_id} V{b.version}] {b.text}")
        return lines

    def set_stub_from_first_answer(self, text: str) -> None:
        """兼容旧调用：走正式切块入库。"""
        if self._svc:
            self._svc.create_from_first_answer(self.room_id, text, activate=True)
            self._sync_from_active()
        else:
            from multi_agent_room.chunker import chunk_text as _ct

            chunks = _ct(text)
            self.doc_version = 1
            self.blocks = [
                type(
                    "B",
                    (),
                    {
                        "block_id": _block_id(i + 1),
                        "version": 1,
                        "text": c.text,
                    },
                )()
                for i, c in enumerate(chunks)
            ]
