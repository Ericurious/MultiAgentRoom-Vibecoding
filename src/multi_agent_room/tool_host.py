"""T-M10：ToolHost — 发现、校验、鉴权、执行、回执。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from multi_agent_room.event_bus import EventBus
from multi_agent_room.file_tools import (
    file_delete,
    file_list,
    file_read,
    file_write,
    glob_search,
    search_replace,
)
from multi_agent_room.logging_setup import log_event
from multi_agent_room.mcp_host import McpHost
from multi_agent_room.redact import redact_secrets
from multi_agent_room.schema_validate import SchemaError, validate_args
from multi_agent_room.skill_registry import (
    SkillRegistry,
    builtin_skills,
)
from multi_agent_room.terminal_tools import run_terminal

# 沟通前默认只读阶段（spec §5.3.2）
READONLY_PHASES = frozenset(
    {
        "Campaign",
        "AwaitingFirstAnswer",
        "ReviewOpen",
        "AwaitingJudge",
        "ConfirmOpen",
        "Frozen",
        "AwaitingUserClarify",
        "AwaitingUserEscalation",
    }
)

WRITE_OK_PHASES = frozenset({"Final", "Idle"})


@dataclass
class ToolResult:
    ok: bool
    tool_name: str
    message: str
    code: str = ""
    exit_code: Optional[int] = None
    paths: list[str] = field(default_factory=list)
    diff_summary: str = ""
    data: Any = None
    mcp_id: str = ""

    def to_room_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool_name,
            "code": self.code,
            "message": self.message,
            "exit_code": self.exit_code,
            "paths": list(self.paths),
            "diff_summary": self.diff_summary,
            "mcp_id": self.mcp_id,
        }


@dataclass
class RoomToolContext:
    room_id: str
    phase: str
    workspace_path: Optional[str] = None
    gate_passed: bool = False
    write_token: Optional[dict[str, Any]] = None
    agent_id: Optional[str] = None


class ToolHost:
    def __init__(
        self,
        *,
        bus: Optional[EventBus] = None,
        skills: Optional[SkillRegistry] = None,
        mcp: Optional[McpHost] = None,
    ) -> None:
        self.bus = bus or EventBus(persist=False)
        self.skills = skills or SkillRegistry()
        self.mcp = mcp or McpHost()
        self._pending_confirm: dict[str, dict[str, Any]] = {}
        if not self.skills.list_skills():
            for sk in builtin_skills():
                self.skills.register(sk)

    def can_write(self, ctx: RoomToolContext) -> tuple[bool, str]:
        if ctx.gate_passed:
            return True, "gate_passed"
        if ctx.write_token:
            exp = ctx.write_token.get("exp")
            if exp is None or float(exp) > time.time():
                return True, "write_token"
        if ctx.phase in WRITE_OK_PHASES and ctx.gate_passed:
            return True, "final"
        if ctx.phase in READONLY_PHASES:
            return False, "审阅/沟通阶段默认只读，禁止写盘"
        return False, "当前阶段无写权限（需 JudgeApprove 或 writeToken）"

    def invoke_skill(
        self,
        skill_id: str,
        args: dict[str, Any],
        ctx: RoomToolContext,
        *,
        user_confirmed: bool = False,
        timeline_append=None,
    ) -> ToolResult:
        """统一入口：授权 → schema → 阶段门控 → 高危确认 → 执行 → ToolReceipt。"""
        ok_auth, auth_msg = self.skills.check_trigger(
            skill_id, room_id=ctx.room_id, agent_id=ctx.agent_id
        )
        if not ok_auth:
            return self._fail(
                ctx,
                skill_id,
                "unauthorized",
                auth_msg,
                timeline_append=timeline_append,
            )

        sk = self.skills.get(skill_id)
        assert sk is not None

        try:
            validate_args(sk.schema or {}, args or {})
        except SchemaError as exc:
            return self._fail(
                ctx,
                sk.tool_name,
                exc.code,
                exc.message,
                timeline_append=timeline_append,
            )

        if sk.risk in ("write", "high"):
            allowed, why = self.can_write(ctx)
            if not allowed:
                return self._fail(
                    ctx,
                    sk.tool_name,
                    "readonly_phase",
                    why,
                    timeline_append=timeline_append,
                )

        if sk.risk == "high" and not user_confirmed:
            from multi_agent_room.sec_guard import SecViolation, assert_high_risk_confirmed

            try:
                assert_high_risk_confirmed(False)
            except SecViolation as exc:
                key = f"{ctx.room_id}:{skill_id}"
                self._pending_confirm[key] = {
                    "skill_id": skill_id,
                    "args": dict(args),
                    "agent_id": ctx.agent_id,
                    "ts": time.time(),
                }
                return self._fail(
                    ctx,
                    sk.tool_name,
                    exc.code,
                    exc.message,
                    timeline_append=timeline_append,
                )

        try:
            result = self._dispatch(sk.tool_name, args, ctx)
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                ctx,
                sk.tool_name,
                "exec_error",
                redact_secrets(str(exc)),
                timeline_append=timeline_append,
            )

        self._emit_receipt(ctx, result, timeline_append=timeline_append)
        return result

    def confirm_and_invoke(
        self,
        skill_id: str,
        ctx: RoomToolContext,
        *,
        timeline_append=None,
    ) -> ToolResult:
        key = f"{ctx.room_id}:{skill_id}"
        pending = self._pending_confirm.pop(key, None)
        if not pending:
            return self._fail(
                ctx,
                skill_id,
                "no_pending",
                "无待确认的高危调用",
                timeline_append=timeline_append,
            )
        return self.invoke_skill(
            skill_id,
            pending["args"],
            ctx,
            user_confirmed=True,
            timeline_append=timeline_append,
        )

    def _dispatch(
        self, tool_name: str, args: dict[str, Any], ctx: RoomToolContext
    ) -> ToolResult:
        if tool_name == "file.read":
            if not ctx.workspace_path:
                raise ValueError("房间未绑定工作区")
            data = file_read(ctx.workspace_path, str(args["path"]))
            return ToolResult(
                ok=True,
                tool_name=tool_name,
                message="读文件成功",
                paths=[data["path"]],
                data=data,
            )
        if tool_name == "dir.list":
            if not ctx.workspace_path:
                raise ValueError("房间未绑定工作区")
            data = file_list(ctx.workspace_path, str(args.get("path") or "."))
            return ToolResult(
                ok=True,
                tool_name=tool_name,
                message=f"列出 {len(data.get('entries') or [])} 项",
                data=data,
            )
        if tool_name == "file.write":
            if not ctx.workspace_path:
                raise ValueError("房间未绑定工作区")
            data = file_write(
                ctx.workspace_path, str(args["path"]), str(args["content"])
            )
            return ToolResult(
                ok=True,
                tool_name=tool_name,
                message="写文件成功",
                paths=[data["path"]],
                diff_summary=data.get("diff_summary", ""),
                data=data,
            )
        if tool_name == "search.replace":
            if not ctx.workspace_path:
                raise ValueError("房间未绑定工作区")
            data = search_replace(
                ctx.workspace_path,
                str(args["path"]),
                str(args["old_string"]),
                str(args["new_string"]),
                replace_all=bool(args.get("replace_all")),
            )
            return ToolResult(
                ok=True,
                tool_name=tool_name,
                message=f"替换 {data.get('replacements', 0)} 处",
                paths=[data.get("path") or ""],
                data=data,
            )
        if tool_name == "file.delete":
            if not ctx.workspace_path:
                raise ValueError("房间未绑定工作区")
            data = file_delete(ctx.workspace_path, str(args["path"]))
            return ToolResult(
                ok=True,
                tool_name=tool_name,
                message="删除成功",
                paths=[data["path"]],
                data=data,
            )
        if tool_name == "glob.search":
            if not ctx.workspace_path:
                raise ValueError("房间未绑定工作区")
            data = glob_search(
                ctx.workspace_path, str(args.get("pattern") or "**/*")
            )
            return ToolResult(
                ok=True,
                tool_name=tool_name,
                message=f"匹配 {len(data.get('matches') or [])} 项",
                data=data,
            )
        if tool_name == "terminal.run":
            argv = list(args["argv"])
            timeout = float(args.get("timeout_sec") or 30)
            data = run_terminal(
                argv,
                cwd=ctx.workspace_path,
                timeout_sec=timeout,
            )
            return ToolResult(
                ok=bool(data.get("ok")),
                tool_name=tool_name,
                message=(
                    "终端超时已杀进程"
                    if data.get("timed_out")
                    else f"exit={data.get('exit_code')}"
                ),
                exit_code=data.get("exit_code"),
                code="timed_out" if data.get("timed_out") else "",
                data=data,
            )
        if tool_name == "mcp.call":
            sid = str(args["server_id"])
            tool = str(args["tool"])
            data = self.mcp.call_tool(sid, tool, args.get("arguments") or {})
            return ToolResult(
                ok=bool(data.get("ok", True)),
                tool_name=tool_name,
                message="MCP 调用完成",
                mcp_id=sid,
                data=data,
            )
        raise ValueError(f"未知工具: {tool_name}")

    def _fail(
        self,
        ctx: RoomToolContext,
        tool_name: str,
        code: str,
        message: str,
        *,
        timeline_append=None,
    ) -> ToolResult:
        safe = redact_secrets(message)
        result = ToolResult(
            ok=False, tool_name=tool_name, message=safe, code=code
        )
        log_event(
            "tool_deny",
            f"{tool_name} {code}: {safe}",
            room_id=ctx.room_id,
        )
        self._emit_receipt(ctx, result, timeline_append=timeline_append)
        return result

    def _emit_receipt(
        self,
        ctx: RoomToolContext,
        result: ToolResult,
        *,
        timeline_append=None,
    ) -> None:
        self.bus.publish_tool_receipt(
            ctx.room_id,
            tool_name=result.tool_name,
            ok=result.ok,
            exit_code=result.exit_code,
            paths=result.paths,
            diff_summary=result.diff_summary,
            mcp_id=result.mcp_id,
            code=result.code,
            message=result.message,
        )
        if timeline_append:
            try:
                timeline_append(
                    ctx.room_id,
                    "ToolReceipt",
                    f"{result.tool_name} ok={result.ok} {result.code or result.message}",
                )
            except Exception:  # noqa: BLE001
                pass
