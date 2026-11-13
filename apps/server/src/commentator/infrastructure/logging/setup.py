"""Structured stdout/file logging with selectable retention."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from .config import LogConfig

OPTIONAL_FIELDS = (
    "event", "requestId", "gameId", "stateVersion", "turnId",
    "durationMs", "errorCode", "retention", "retentionDays",
    "maxBytes", "backupCount",
    "method", "path", "statusCode",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in OPTIONAL_FIELDS:
            field = getattr(record, name, None)
            if field is not None:
                value[name] = field
        if record.exc_info and record.exc_info[0] is not None:
            value["exceptionType"] = record.exc_info[0].__name__
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def prune_expired_logs(
    directory: Path, retention_days: int, *, now: datetime | None = None
) -> list[Path]:
    if not directory.exists():
        return []
    reference = now or datetime.now(UTC)
    cutoff = reference - timedelta(days=retention_days)
    removed: list[Path] = []
    for path in directory.glob("commentator*.jsonl*"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def apply_retention(config: LogConfig, *, now: datetime | None = None) -> list[Path]:
    if config.retention == "permanent":
        return []
    return prune_expired_logs(config.directory, config.retention_days, now=now)


def _file_handler(config: LogConfig) -> logging.Handler:
    log_path = config.directory / "commentator.jsonl"
    if config.retention == "rotate":
        apply_retention(config)
        return RotatingFileHandler(
            log_path, maxBytes=config.max_bytes,
            backupCount=config.backup_count, encoding="utf-8",
        )
    return TimedRotatingFileHandler(
        log_path, when="midnight", interval=1, backupCount=0,
        encoding="utf-8", utc=True,
    )


def configure_logging(config: LogConfig) -> None:
    config.directory.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    file_handler = _file_handler(config)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.INFO)
    root.addHandler(stdout_handler)
    root.addHandler(file_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("commentator.startup").info(
        "logging configured",
        extra={
            "event": "logging_configured",
            "retention": config.retention,
            "retentionDays": config.retention_days,
            "maxBytes": config.max_bytes,
            "backupCount": config.backup_count,
        },
    )
