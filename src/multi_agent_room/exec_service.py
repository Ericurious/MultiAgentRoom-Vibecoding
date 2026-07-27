"""T-M11：执行阶段补充（T0/T1/T2）— 环境画像 / 静态检查 / 沙盒 / 回灌 / 结果审查 / 回门禁。"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.room import Room
from multi_agent_room.sandbox_runner import SandboxResult, SandboxRunner
from multi_agent_room.skeleton_gate import SkeletonCheck, SkeletonQuota, check_skeleton

ExecMode = Literal["T0", "T1", "T2"]


@dataclass
class EnvProfile:
    """T-M11-02：按任务画像；文档任务不强制网页踩点。"""

    task_kind: str  # doc | code | mixed | unknown
    needs_files: bool = False
    needs_deps: list[str] = field(default_factory=list)
    needs_web: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskKind": self.task_kind,
            "needsFiles": self.needs_files,
            "needsDeps": list(self.needs_deps),
            "needsWeb": self.needs_web,
            "notes": self.notes,
        }


@dataclass
class StaticCheckResult:
    ok: bool
    message: str
    code: str = ""
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "diagnostics": list(self.diagnostics),
        }


@dataclass
class ErrorFeedback:
    """T-M11-06 报错回灌模板。"""

    error_type: str
    message: str
    stack: str
    related_files: list[str] = field(default_factory=list)

    def prompt_text(self) -> str:
        return (
            f"刚刚写的代码报错 {self.error_type}，根据报错重新修改。"
            f" message={self.message}"
            f" stack={self.stack[:2000]}"
            f" related_files={','.join(self.related_files)}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": self.message,
            "stack": self.stack,
            "related_files": list(self.related_files),
            "prompt": self.prompt_text(),
        }


@dataclass
class ResultReview:
    """T-M11-07：goal / current_result / gap / pass|fail。"""

    goal: str
    current_result: str
    gap: str
    verdict: Literal["pass", "fail"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "current_result": self.current_result,
            "gap": self.gap,
            "verdict": self.verdict,
        }


@dataclass
class ExecRunResult:
    ok: bool
    mode: ExecMode
    message: str
    code: str = ""
    profile: Optional[EnvProfile] = None
    static: Optional[StaticCheckResult] = None
    skeleton: Optional[SkeletonCheck] = None
    sandbox: Optional[SandboxResult] = None
    feedback: Optional[ErrorFeedback] = None
    review: Optional[ResultReview] = None
    completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "code": self.code,
            "message": self.message,
            "profile": self.profile.to_dict() if self.profile else None,
            "static": self.static.to_dict() if self.static else None,
            "skeleton": None
            if not self.skeleton
            else {
                "ok": self.skeleton.ok,
                "code": self.skeleton.code,
                "message": self.skeleton.message,
            },
            "sandbox": self.sandbox.to_dict() if self.sandbox else None,
            "feedback": self.feedback.to_dict() if self.feedback else None,
            "review": self.review.to_dict() if self.review else None,
            "completed": self.completed,
        }


def build_env_profile(question: str, *, has_code_hint: bool = False) -> EnvProfile:
    q = (question or "").lower()
    doc_hints = ("文档", "总结", "说明", "readme", "markdown", "md", "写一篇")
    code_hints = ("代码", "脚本", "python", "node", "程序", "函数", "bug", ".py", ".js")
    web_hints = ("网页", "浏览器", "http", "爬取", "网站")
    is_doc = any(h in q for h in doc_hints)
    is_code = has_code_hint or any(h in q for h in code_hints)
    needs_web = any(h in q for h in web_hints)
    if is_doc and not is_code:
        return EnvProfile(
            task_kind="doc",
            needs_files=True,
            needs_web=False,
            notes="文档任务：不强制网页踩点",
        )
    if is_code:
        deps = []
        if "python" in q or ".py" in q:
            deps.append("python")
        if "node" in q or ".js" in q:
            deps.append("node")
        return EnvProfile(
            task_kind="code",
            needs_files=True,
            needs_deps=deps or ["python"],
            needs_web=needs_web,
            notes="代码任务：检查本机运行时",
        )
    if needs_web:
        return EnvProfile(
            task_kind="mixed",
            needs_web=True,
            notes="含网页依赖（P2 浏览器；P1b 不强制）",
        )
    return EnvProfile(task_kind="unknown", notes="默认不强制执行补充")


def static_check_source(
    source: str, *, filename: str = "main.py"
) -> StaticCheckResult:
    """入沙盒前语法/结构检查。"""
    ext = Path(filename).suffix.lower()
    diags: list[str] = []
    if not (source or "").strip():
        return StaticCheckResult(
            ok=False, code="empty_source", message="源码为空", diagnostics=["empty"]
        )
    if ext == ".py":
        try:
            ast.parse(source)
        except SyntaxError as exc:
            diags.append(f"SyntaxError: {exc.msg} line={exc.lineno}")
            return StaticCheckResult(
                ok=False,
                code="syntax_error",
                message=f"静态检查失败: {exc.msg}",
                diagnostics=diags,
            )
    elif ext in (".js", ".mjs"):
        # 轻量：括号平衡
        if source.count("{") != source.count("}"):
            diags.append("unbalanced braces")
            return StaticCheckResult(
                ok=False,
                code="structure_error",
                message="JS 花括号不平衡",
                diagnostics=diags,
            )
    return StaticCheckResult(ok=True, message="static ok", diagnostics=diags)


def review_result(
    *,
    goal: str,
    current_result: str,
    gap: str = "",
) -> ResultReview:
    """空结果 → fail。"""
    cur = (current_result or "").strip()
    if not cur:
        return ResultReview(
            goal=goal or "",
            current_result="",
            gap=gap or "结果为空",
            verdict="fail",
        )
    g = (goal or "").strip()
    # 简单：有非空结果且未显式标失败
    auto_gap = gap
    if g and g.lower() not in cur.lower() and not gap:
        auto_gap = "输出未明显覆盖目标关键词"
    verdict: Literal["pass", "fail"] = "pass"
    if auto_gap and "失败" in auto_gap:
        verdict = "fail"
    return ResultReview(
        goal=g, current_result=cur, gap=auto_gap, verdict=verdict
    )


def can_mark_complete(room: Room) -> tuple[bool, str]:
    """T-M11-08：无 M7（FinalCommitted）不得标任务完成。"""
    if not room.gate_passed:
        return False, "无 JudgeApprove / 门禁未通过，不能标任务完成"
    if not (room.final_reply or "").strip():
        return False, "无 FinalCommitted（最终回复槽为空），不能标任务完成"
    return True, "ok"


def build_error_feedback(sb: SandboxResult) -> ErrorFeedback:
    return ErrorFeedback(
        error_type=sb.error_type or sb.code or "sandbox_error",
        message=sb.message or sb.stderr[:500],
        stack=sb.stack or sb.stderr,
        related_files=list(sb.related_files),
    )


class ExecService:
    """按需启用执行补充；T0 可跳过。"""

    def __init__(
        self,
        *,
        sandbox: Optional[SandboxRunner] = None,
        skeleton_quota: Optional[SkeletonQuota] = None,
    ) -> None:
        self.sandbox = sandbox or SandboxRunner()
        self.skeleton_quota = skeleton_quota or SkeletonQuota()
        self._enabled: dict[str, bool] = {}
        self._last: dict[str, ExecRunResult] = {}
        self._complete_flags: dict[str, bool] = {}

    def set_enabled(self, room_id: str, enabled: bool) -> None:
        self._enabled[room_id] = enabled
        log_event("exec_toggle", f"enabled={enabled}", room_id=room_id)

    def is_enabled(self, room_id: str) -> bool:
        return bool(self._enabled.get(room_id, False))

    def last(self, room_id: str) -> Optional[ExecRunResult]:
        return self._last.get(room_id)

    def is_task_complete(self, room_id: str) -> bool:
        return bool(self._complete_flags.get(room_id))

    def mark_complete(self, room: Room) -> tuple[bool, str]:
        ok, msg = can_mark_complete(room)
        if not ok:
            self._complete_flags[room.room_id] = False
            return False, msg
        self._complete_flags[room.room_id] = True
        log_event("task_complete", "marked", room_id=room.room_id)
        return True, "ok"

    def run_t0(self, room: Room) -> ExecRunResult:
        """纯问答：不启用执行，仍可走 Final。"""
        profile = build_env_profile(room.pinned_question or "")
        result = ExecRunResult(
            ok=True,
            mode="T0",
            message="T0：未启用执行补充",
            code="skipped",
            profile=profile,
        )
        self._last[room.room_id] = result
        self.set_enabled(room.room_id, False)
        return result

    def run_t2(
        self,
        room: Room,
        source: str,
        *,
        filename: str = "main.py",
        goal: str = "",
        extra_files: Optional[dict[str, str]] = None,
    ) -> ExecRunResult:
        """T2：环境画像 → 骨架 → 静态检查 → 沙盒 → 回灌/审查。"""
        self.set_enabled(room.room_id, True)
        profile = build_env_profile(
            room.pinned_question or "", has_code_hint=True
        )
        # 文档任务不强制网页
        if profile.task_kind == "doc":
            profile.needs_web = False

        sk = check_skeleton(
            source, quota=self.skeleton_quota, label=filename
        )
        if sk.rejected:
            result = ExecRunResult(
                ok=False,
                mode="T2",
                message=sk.message,
                code="skeleton_overflow",
                profile=profile,
                skeleton=sk,
            )
            self._last[room.room_id] = result
            return result

        st = static_check_source(source, filename=filename)
        if not st.ok:
            result = ExecRunResult(
                ok=False,
                mode="T2",
                message=st.message,
                code=st.code or "static_failed",
                profile=profile,
                skeleton=sk,
                static=st,
            )
            # 不得进入沙盒成功态：不调用 sandbox
            self._last[room.room_id] = result
            return result

        sb = self.sandbox.run_code(
            source,
            filename=filename,
            workspace_path=room.workspace_path,
            extra_files=extra_files,
        )
        feedback = None
        if not sb.ok:
            feedback = build_error_feedback(sb)

        review = review_result(
            goal=goal or (room.pinned_question or ""),
            current_result=(sb.stdout or "").strip()
            if sb.ok
            else (sb.stderr or sb.message),
            gap="" if sb.ok else (feedback.prompt_text() if feedback else sb.message),
        )
        if not sb.ok:
            review.verdict = "fail"
        if not (sb.stdout or "").strip() and sb.ok:
            # 空 stdout 也 fail
            review = review_result(
                goal=goal or "",
                current_result="",
                gap="沙盒无输出",
            )

        ok = sb.ok and review.verdict == "pass"
        result = ExecRunResult(
            ok=ok,
            mode="T2",
            message="T2 ok" if ok else (feedback.prompt_text() if feedback else sb.message),
            code="ok" if ok else (sb.code or "t2_failed"),
            profile=profile,
            skeleton=sk,
            static=st,
            sandbox=sb,
            feedback=feedback,
            review=review,
        )
        self._last[room.room_id] = result
        return result
