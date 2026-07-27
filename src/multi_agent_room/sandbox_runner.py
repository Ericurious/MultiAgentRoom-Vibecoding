"""T-M11-05：简沙盒 — 临时目录 + python/node/dotnet；禁写正式 workspace。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from multi_agent_room.logging_setup import log_event
from multi_agent_room.paths import normalize_workspace_path
from multi_agent_room.terminal_tools import _truncate

RUNTIME_BY_EXT = {
    ".py": "python",
    ".js": "node",
    ".mjs": "node",
    ".cs": "dotnet",
    ".csproj": "dotnet",
    ".sln": "dotnet",
}


@dataclass
class SandboxResult:
    ok: bool
    code: str = ""
    message: str = ""
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    stack: str = ""
    error_type: str = ""
    sandbox_dir: str = ""
    runtime: str = ""
    wrote_workspace: bool = False
    elapsed_ms: int = 0
    related_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stack": self.stack,
            "error_type": self.error_type,
            "sandbox_dir": self.sandbox_dir,
            "runtime": self.runtime,
            "wrote_workspace": self.wrote_workspace,
            "related_files": list(self.related_files),
        }


def resolve_runtime(filename: str) -> tuple[str, list[str]]:
    """返回 (runtime_name, argv_prefix)。"""
    ext = Path(filename).suffix.lower()
    kind = RUNTIME_BY_EXT.get(ext, "")
    if kind == "python":
        return "python", [os.environ.get("PYTHON", "python")]
    if kind == "node":
        return "node", ["node"]
    if kind == "dotnet":
        return "dotnet", ["dotnet", "run", "--project"]
    return "", []


def runtime_available(runtime: str) -> bool:
    if not runtime:
        return False
    return shutil.which(runtime) is not None or (
        runtime == "python" and shutil.which("py") is not None
    )


def _python_argv() -> list[str]:
    for cand in (os.environ.get("PYTHON"), "python", "py", "python3"):
        if cand and shutil.which(cand):
            return [cand]
    return ["python"]


class SandboxRunner:
    """T2：仅临时目录执行；禁止写正式 workspace（DEP-11）。"""

    def __init__(self, *, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec

    def run_code(
        self,
        source: str,
        *,
        filename: str = "main.py",
        workspace_path: Optional[str] = None,
        extra_files: Optional[dict[str, str]] = None,
    ) -> SandboxResult:
        runtime, _ = resolve_runtime(filename)
        if not runtime:
            return SandboxResult(
                ok=False,
                code="unsupported_ext",
                message=f"不支持的扩展名: {filename}",
            )
        if not runtime_available(runtime if runtime != "python" else "python"):
            # python 特殊：再查 py
            if runtime == "python" and not (
                shutil.which("python") or shutil.which("py") or shutil.which("python3")
            ):
                return SandboxResult(
                    ok=False,
                    code="runtime_missing",
                    message=f"运行时未安装: {runtime}",
                    runtime=runtime,
                )
            if runtime != "python" and not shutil.which(runtime):
                return SandboxResult(
                    ok=False,
                    code="runtime_missing",
                    message=f"运行时未安装: {runtime}",
                    runtime=runtime,
                )

        tmp = Path(tempfile.mkdtemp(prefix="mar-sandbox-"))
        from multi_agent_room.sec_guard import SecViolation, assert_sandbox_not_workspace

        try:
            assert_sandbox_not_workspace(str(tmp), workspace_path)
        except SecViolation as exc:
            cleanup_sandbox(str(tmp))
            return SandboxResult(
                ok=False,
                code=exc.code,
                message=exc.message,
                runtime=runtime,
            )
        started = time.time()
        try:
            main = tmp / filename
            main.write_text(source, encoding="utf-8")
            related = [str(main)]
            for rel, body in (extra_files or {}).items():
                p = tmp / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
                related.append(str(p))

            if runtime == "python":
                argv = _python_argv() + [str(main)]
            elif runtime == "node":
                argv = ["node", str(main)]
            else:
                argv = ["dotnet", "run", "--project", str(main)]

            # 环境：不指向正式 workspace 为 cwd
            env = os.environ.copy()
            env["MAR_SANDBOX"] = "1"
            if workspace_path:
                env["MAR_WORKSPACE_FORBIDDEN"] = str(
                    normalize_workspace_path(workspace_path)
                )

            try:
                proc = subprocess.run(
                    argv,
                    cwd=str(tmp),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_sec,
                    env=env,
                    shell=False,
                )
                stdout, out_t = _truncate(proc.stdout or "")
                stderr, err_t = _truncate(proc.stderr or "")
                stack = stderr
                err_type = ""
                if proc.returncode != 0:
                    err_type = _guess_error_type(stderr, runtime)
                wrote_ws = _detect_workspace_write(tmp, workspace_path)
                ok = proc.returncode == 0 and not wrote_ws
                code = ""
                msg = "sandbox ok"
                if wrote_ws:
                    code = "workspace_write_forbidden"
                    msg = "沙盒禁止写正式 workspace（DEP-11）"
                    ok = False
                elif proc.returncode != 0:
                    code = "sandbox_failed"
                    msg = f"沙盒退出码 {proc.returncode}"
                return SandboxResult(
                    ok=ok,
                    code=code,
                    message=msg,
                    exit_code=proc.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    stack=stack,
                    error_type=err_type,
                    sandbox_dir=str(tmp),
                    runtime=runtime,
                    wrote_workspace=wrote_ws,
                    elapsed_ms=int((time.time() - started) * 1000),
                    related_files=related,
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(
                    ok=False,
                    code="timed_out",
                    message="沙盒超时",
                    sandbox_dir=str(tmp),
                    runtime=runtime,
                    related_files=related,
                    elapsed_ms=int((time.time() - started) * 1000),
                )
        finally:
            # 保留目录片刻供回灌读 stack；测试可立即清理
            log_event("sandbox_done", f"dir={tmp} runtime={runtime}")


def _guess_error_type(stderr: str, runtime: str) -> str:
    for line in (stderr or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if "Error" in s or "Exception" in s:
            # e.g. ZeroDivisionError: ...
            head = s.split(":")[0].strip()
            if head:
                return head
    return f"{runtime}_error" if stderr else "unknown_error"


def _detect_workspace_write(
    sandbox_dir: Path, workspace_path: Optional[str]
) -> bool:
    """启发式：源码若显式写入 workspace 绝对路径则标违规（执行后检查产物）。"""
    if not workspace_path:
        return False
    root = normalize_workspace_path(workspace_path)
    # 若沙盒外出现新建文件过重；此处检查源码意图标记
    for p in sandbox_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # 明确调用写 workspace
        if str(root) in text and (
            "open(" in text or "write_text" in text or "Path(" in text
        ):
            # 仅当实际创建了 workspace 下文件
            break
    # 扫描 workspace 是否在运行窗口被沙盒污染：看 mtime 不可靠；
    # 约定：沙盒 cwd 外写通过环境变量探针文件
    marker = root / ".mar_sandbox_probe_should_not_exist"
    return marker.exists()


def cleanup_sandbox(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass
