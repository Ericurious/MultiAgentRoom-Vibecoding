"""T-M10-03：终端白名单 / 超时杀进程 / 输出截断。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from multi_agent_room.logging_setup import log_event

DEFAULT_WHITELIST = frozenset(
    {
        "git",
        "python",
        "python3",
        "py",
        "node",
        "dotnet",
        "where",
        "dir",
        "echo",
        "type",
        "cmd",
    }
)

OUTPUT_LIMIT = 32 * 1024  # 32KB
DEFAULT_TIMEOUT_SEC = 30


def _basename(exe: str) -> str:
    name = Path(exe).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def assert_whitelisted(argv: list[str], allow: Optional[set[str]] = None) -> str:
    if not argv:
        raise ValueError("argv 不能为空")
    allowed = allow or set(DEFAULT_WHITELIST)
    base = _basename(argv[0])
    if base not in allowed:
        raise PermissionError(f"终端可执行名不在白名单: {base}")
    return base


def _truncate(s: str, limit: int = OUTPUT_LIMIT) -> tuple[str, bool]:
    raw = s.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return s, False
    cut = raw[:limit].decode("utf-8", errors="replace")
    return cut, True


def run_terminal(
    argv: list[str],
    *,
    cwd: Optional[str | Path] = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    whitelist: Optional[set[str]] = None,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """执行白名单命令；超时 taskkill；stdout/stderr 各截断 32KB。"""
    base = assert_whitelisted(argv, whitelist)
    work = str(cwd) if cwd else None
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.Popen(
            argv,
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env or os.environ.copy(),
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except OSError as exc:
        raise RuntimeError(f"无法启动进程: {exc}") from exc

    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except Exception:  # noqa: BLE001
            stdout, stderr = "", ""
        code = proc.returncode if proc.returncode is not None else -9
        log_event("terminal_timeout", f"exe={base} pid={proc.pid}")

    out, out_trunc = _truncate(stdout or "")
    err, err_trunc = _truncate(stderr or "")
    return {
        "ok": (not timed_out) and code == 0,
        "exit_code": code,
        "stdout": out,
        "stderr": err,
        "truncated": out_trunc or err_trunc,
        "timed_out": timed_out,
        "elapsed_ms": int((time.time() - started) * 1000),
        "argv": list(argv),
        "exe": base,
    }


def _kill_process_tree(pid: int) -> None:
    """Windows：taskkill /F /T；其它：kill。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        os.kill(pid, 9)
    except OSError:
        pass
