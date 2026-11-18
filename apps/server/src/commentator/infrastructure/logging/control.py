"""Runtime control of the log retention setting and a buffer of recent problems."""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from .config import LogConfig, load_log_config
from .setup import configure_logging

RETENTION_ENV = "COMMENTATOR_LOG_RETENTION"


class RecentProblems(logging.Handler):
    """Keep the latest warnings and errors in memory for the admin status panel.

    Only the message, logger, event and error code are kept, never request bodies.
    """

    def __init__(self, capacity: int = 30) -> None:
        super().__init__(level=logging.WARNING)
        self._items: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._items_lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        item = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", None),
            "errorCode": getattr(record, "errorCode", None),
        }
        with self._items_lock:
            self._items.append(item)

    def items(self) -> list[dict[str, Any]]:
        with self._items_lock:
            return list(reversed(self._items))


class LoggingControl:
    """Owns the logging configuration of the running server.

    ``managed`` controls reconfigure the real handlers (production). Unmanaged
    controls only report the environment configuration (tests, tools).
    """

    def __init__(
        self,
        *,
        settings_path: Path | None,
        default_directory: Path,
        managed: bool,
    ) -> None:
        self.settings_path = settings_path
        self.default_directory = default_directory
        self.managed = managed
        self.problems = RecentProblems()
        self._lock = Lock()
        self._started = False
        self.config = self._load()

    @classmethod
    def unmanaged(cls) -> LoggingControl:
        return cls(settings_path=None, default_directory=Path("var/log"), managed=False)

    @property
    def locked(self) -> bool:
        """An explicit environment variable wins over the admin setting."""
        return RETENTION_ENV in os.environ

    def start(self) -> None:
        """Install handlers (managed) and attach the recent-problems buffer."""
        if self._started:
            return
        self._started = True
        if self.managed:
            configure_logging(self.config)
        logging.getLogger().addHandler(self.problems)

    def stop(self) -> None:
        logging.getLogger().removeHandler(self.problems)
        self._started = False

    def set_retention(self, retention: str) -> LogConfig:
        if retention not in {"rotate", "permanent"}:
            raise ValueError("保存方式は rotate または permanent を指定してください")
        if not self.managed or self.settings_path is None:
            raise PermissionError("このサーバーではログ設定を変更できません")
        if self.locked:
            raise PermissionError(
                f"環境変数 {RETENTION_ENV} で固定されているため、画面からは変更できません"
            )
        with self._lock:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps({"retention": retention}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.settings_path)
            self.config = replace(self.config, retention=retention)
            configure_logging(self.config)  # replaces all root handlers
            logging.getLogger().addHandler(self.problems)
        return self.config

    def status(self) -> dict[str, Any]:
        return {
            "retention": self.config.retention,
            "directory": str(self.config.directory),
            "retentionDays": self.config.retention_days,
            "locked": self.locked,
            "editable": self.managed and self.settings_path is not None and not self.locked,
        }

    def _load(self) -> LogConfig:
        config = load_log_config(default_directory=self.default_directory)
        if self.locked or self.settings_path is None or not self.settings_path.is_file():
            return config
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return config
        retention = saved.get("retention")
        if retention in {"rotate", "permanent"}:
            return replace(config, retention=retention)
        return config
