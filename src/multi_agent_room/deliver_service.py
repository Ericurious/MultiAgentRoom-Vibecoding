"""T-M12：正式交付（点交付 / 授权落盘门禁）。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from multi_agent_room.file_tools import file_write, resolve_in_workspace
from multi_agent_room.logging_setup import log_event
from multi_agent_room.paths import normalize_workspace_path
from multi_agent_room.room import Room


@dataclass
class DeliverItem:
    rel_path: str
    abs_path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.rel_path,
            "absPath": self.abs_path,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass
class DeliverResult:
    ok: bool
    message: str
    code: str = ""
    delivery_id: str = ""
    items: list[DeliverItem] = field(default_factory=list)
    manifest_rel: str = ""
    gate: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "deliveryId": self.delivery_id,
            "gate": self.gate,
            "manifest": self.manifest_rel,
            "items": [i.to_dict() for i in self.items],
        }


def is_final_committed(room: Room) -> bool:
    """FinalCommitted：门禁通过且最终回复槽有正文。"""
    return bool(room.gate_passed and (room.final_reply or "").strip())


def write_token_valid(room: Room, *, now: Optional[float] = None) -> bool:
    tok = room.write_token
    if not tok:
        return False
    exp = tok.get("exp")
    if exp is None:
        return True
    return float(exp) > (now if now is not None else time.time())


def check_deliver_gate(room: Room) -> tuple[bool, str, str]:
    """须 FinalCommitted 或有效 writeToken（spec §5.3.1）。"""
    if is_final_committed(room):
        return True, "FinalCommitted", "ok"
    if write_token_valid(room):
        return True, "writeToken", "ok"
    return False, "", "正式落盘拒绝：须 FinalCommitted 或有效 writeToken"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel_to_workspace(workspace: Path, abs_path: Path) -> str:
    return abs_path.relative_to(workspace).as_posix()


def _write_tracked(workspace: Path, rel: str, content: str) -> DeliverItem:
    body = content if content.endswith("\n") else content + "\n"
    written = file_write(workspace, rel, body)
    p = Path(written["path"])
    text = p.read_text(encoding="utf-8")
    if p.stat().st_size <= 0:
        raise ValueError(f"写入后文件为空: {rel}")
    return DeliverItem(
        rel_path=_rel_to_workspace(workspace, p),
        abs_path=str(p),
        size=p.stat().st_size,
        sha256=_sha256_text(text),
    )


class DeliverService:
    """用户点「交付」触发；不靠关键词猜测。"""

    def deliver(
        self,
        room: Room,
        *,
        content: Optional[str] = None,
        summary: Optional[str] = None,
        rel_dir: str = "delivery",
        filename: str = "final-reply.md",
        extra_files: Optional[dict[str, str]] = None,
        force_path: Optional[str] = None,
    ) -> DeliverResult:
        ok, gate, msg = check_deliver_gate(room)
        if not ok:
            return DeliverResult(ok=False, message=msg, code="gate_denied")

        if not room.workspace_path:
            return DeliverResult(
                ok=False, message="房间未绑定工作区", code="no_workspace"
            )

        workspace = normalize_workspace_path(room.workspace_path)
        body = (content if content is not None else (room.final_reply or "")).strip()
        delivery_id = f"dlv-{uuid.uuid4().hex[:10]}"
        items: list[DeliverItem] = []

        try:
            if force_path is not None:
                # 显式路径（用于越界测试 / 自定义主文件）
                if not body:
                    body = "(authorized empty placeholder)\n"
                items.append(_write_tracked(workspace, force_path, body))
            elif body:
                main_rel = f"{rel_dir.rstrip('/')}/{filename}"
                items.append(_write_tracked(workspace, main_rel, body))

            if summary is not None:
                sum_rel = f"{rel_dir.rstrip('/')}/summary.md"
                items.append(
                    _write_tracked(workspace, sum_rel, summary.strip() or "(空总结)")
                )

            for rel, text in (extra_files or {}).items():
                items.append(_write_tracked(workspace, rel, text))

            if not items:
                return DeliverResult(
                    ok=False,
                    message="交付正文为空",
                    code="empty_content",
                    gate=gate,
                )

            manifest = {
                "deliveryId": delivery_id,
                "roomId": room.room_id,
                "gate": gate,
                "createdAt": time.time(),
                "items": [i.to_dict() for i in items],
            }
            man_rel = f"{rel_dir.rstrip('/')}/manifest.json"
            man_body = json.dumps(manifest, ensure_ascii=False, indent=2)
            file_write(workspace, man_rel, man_body + "\n")
            man_abs = resolve_in_workspace(workspace, man_rel)

        except PermissionError as exc:
            return DeliverResult(
                ok=False,
                message=str(exc),
                code="path_escape",
                gate=gate,
                delivery_id=delivery_id,
            )
        except Exception as exc:  # noqa: BLE001
            return DeliverResult(
                ok=False,
                message=str(exc),
                code="deliver_error",
                gate=gate,
                delivery_id=delivery_id,
            )

        log_event(
            "deliver_ok",
            f"id={delivery_id} n={len(items)} gate={gate}",
            room_id=room.room_id,
        )
        return DeliverResult(
            ok=True,
            message="交付成功",
            code="ok",
            delivery_id=delivery_id,
            items=items,
            manifest_rel=_rel_to_workspace(workspace, man_abs),
            gate=gate,
        )

    def verify_manifest_on_disk(
        self, workspace: str | Path, manifest_rel: str
    ) -> tuple[bool, str]:
        """M12-D：清单与磁盘一致。"""
        root = normalize_workspace_path(workspace)
        try:
            path = resolve_in_workspace(root, manifest_rel)
        except PermissionError as exc:
            return False, str(exc)
        if not path.is_file():
            return False, "清单文件不存在"
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("items") or []:
            rel = item.get("path") or ""
            try:
                p = resolve_in_workspace(root, rel)
            except PermissionError:
                return False, f"越界项: {rel}"
            if not p.is_file():
                return False, f"缺失: {rel}"
            size = p.stat().st_size
            if int(item.get("size") or -1) != size:
                return False, f"大小不一致: {rel}"
            digest = _sha256_text(p.read_text(encoding="utf-8"))
            if item.get("sha256") and item["sha256"] != digest:
                return False, f"校验和不符: {rel}"
        return True, "ok"
