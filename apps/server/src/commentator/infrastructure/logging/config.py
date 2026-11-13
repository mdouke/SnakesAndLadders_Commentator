"""Validated environment configuration for local application logs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LogConfig:
    retention: str = "rotate"
    directory: Path = Path("var/log")
    retention_days: int = 30
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} は正の整数で指定してください: {raw}") from error
    if value <= 0:
        raise ValueError(f"{name} は正の整数で指定してください: {raw}")
    return value


def load_log_config(*, default_directory: Path | None = None) -> LogConfig:
    retention = os.environ.get("COMMENTATOR_LOG_RETENTION", "rotate").strip().lower()
    if retention not in {"rotate", "permanent"}:
        raise ValueError(
            "COMMENTATOR_LOG_RETENTION は rotate または permanent で指定してください: "
            f"{retention}"
        )
    directory = Path(os.environ.get(
        "COMMENTATOR_LOG_DIR", str(default_directory or Path("var/log"))
    )).expanduser()
    return LogConfig(
        retention=retention,
        directory=directory,
        retention_days=_positive_int("COMMENTATOR_LOG_RETENTION_DAYS", 30),
        max_bytes=_positive_int("COMMENTATOR_LOG_MAX_BYTES", 10 * 1024 * 1024),
        backup_count=_positive_int("COMMENTATOR_LOG_BACKUP_COUNT", 5),
    )
