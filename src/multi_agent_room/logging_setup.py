"""ENV-07a 最小运行日志（T-ENV-04a）。

记录：phase 变更、探活结果码、协议拒收、合入/打回、JudgeApprove、致命异常。
禁止写入 API Key 明文。UTF-8 文本，按日滚动文件名。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from multi_agent_room.paths import get_logs_dir

_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|secret|password)\s*[:=]\s*([^\s,;]+)"
)

LOGGER_NAME = "multi_agent_room"


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return _SECRET_PATTERN.sub(r"\1=***", original)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化控制台 + 文件日志；幂等。"""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    fmt = RedactingFormatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_path = get_logs_dir() / f"app-{datetime.now():%Y%m%d}.log"
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_event(event: str, message: str, *, level: int = logging.INFO, **extra: Any) -> None:
    """结构化事件日志入口。事件名写入消息前缀，避免 LogRecord 保留字冲突。"""
    logger = get_logger()
    if not logger.handlers:
        setup_logging()
    if extra:
        safe = {k: ("***" if _looks_secret(k) else v) for k, v in extra.items()}
        message = f"{message} | {safe}"
    logger.log(level, "[%s] %s", event, message)


def _looks_secret(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in ("key", "secret", "password", "token", "authorization"))


def log_phase_change(from_phase: str, to_phase: str, room_id: str | None = None) -> None:
    log_event(
        "phase_change",
        f"{from_phase} -> {to_phase}",
        room_id=room_id,
    )


def log_probe_result(config_id: str, code: str, detail: str = "") -> None:
    log_event("probe_result", f"config={config_id} code={code} {detail}".strip())


def log_protocol_reject(reason: str, patch_id: str | None = None) -> None:
    log_event("protocol_reject", reason, patch_id=patch_id)


def log_merge_accept(patch_id: str, target: str) -> None:
    log_event("merge_accept", f"patch={patch_id} target={target}")


def log_reject_verdict(kind: str, detail: str) -> None:
    log_event("verdict_reject", f"{kind}: {detail}")


def log_judge_approve(room_id: str) -> None:
    log_event("judge_approve", f"room={room_id}")


def log_fatal(exc: BaseException) -> None:
    logger = get_logger()
    if not logger.handlers:
        setup_logging()
    logger.exception("[fatal] %s", exc)


def latest_log_file() -> Path | None:
    logs = get_logs_dir()
    files = sorted(logs.glob("app-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None
