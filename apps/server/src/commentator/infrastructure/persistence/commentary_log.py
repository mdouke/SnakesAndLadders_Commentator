"""Stored commentary requests: prompt, full response, model, timing and errors.

One row per generation request. ``status`` tells what happened on screen:

- ``pending``    generation is running
- ``displayed``  shown (and spoken) as the current comment
- ``discarded``  finished after its turn stopped being the newest turn; never shown
- ``superseded`` was shown, then replaced (regeneration, correction, undo)

``outcome`` tells how the text was produced: ``generated``, ``fallback`` (the template
comment replaced a failed, refused or unusable response) or ``error``.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

COLUMNS = (
    "id", "game_id", "turn_id", "state_version", "revision", "trigger", "provider",
    "model", "prompt_version", "system_prompt", "user_prompt", "status", "outcome",
    "response_text", "final_text", "fallback_reason", "error_code", "error_message",
    "attempts", "errors", "provider_request_ids", "started_at", "finished_at",
    "duration_ms",
)
_JSON_COLUMNS = {"errors", "provider_request_ids"}
_CAMEL = {
    "game_id": "gameId", "turn_id": "turnId", "state_version": "stateVersion",
    "prompt_version": "promptVersion", "system_prompt": "systemPrompt",
    "user_prompt": "userPrompt", "response_text": "responseText", "final_text": "finalText",
    "fallback_reason": "fallbackReason", "error_code": "errorCode",
    "error_message": "errorMessage", "provider_request_ids": "providerRequestIds",
    "started_at": "startedAt", "finished_at": "finishedAt", "duration_ms": "durationMs",
}


def to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {_CAMEL.get(key, key): row.get(key) for key in COLUMNS}


class CommentaryLog(Protocol):
    def start(self, record: dict[str, Any]) -> None: ...

    def update(self, request_id: str, **fields: Any) -> None: ...

    def get(self, request_id: str) -> dict[str, Any] | None: ...

    def list(self, *, game_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...


class MemoryCommentaryLog:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def start(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._rows[record["id"]] = {key: record.get(key) for key in COLUMNS}

    def update(self, request_id: str, **fields: Any) -> None:
        with self._lock:
            if request_id in self._rows:
                self._rows[request_id].update(fields)

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(request_id)
            return dict(row) if row else None

    def list(self, *, game_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                dict(row) for row in self._rows.values()
                if game_id is None or row["game_id"] == game_id
            ]
        return list(reversed(rows))[:limit]


class SQLiteCommentaryLog:
    """Lives in the game database file but in its own table and connections."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS commentary_requests (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    game_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    trigger TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    prompt_version TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    user_prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    outcome TEXT,
                    response_text TEXT,
                    final_text TEXT,
                    fallback_reason TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    errors TEXT NOT NULL DEFAULT '[]',
                    provider_request_ids TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms REAL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS commentary_requests_game "
                "ON commentary_requests (game_id, seq)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _encode(key: str, value: Any) -> Any:
        return json.dumps(value or [], ensure_ascii=False) if key in _JSON_COLUMNS else value

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        value = {key: row[key] for key in COLUMNS}
        for key in _JSON_COLUMNS:
            value[key] = json.loads(value[key] or "[]")
        return value

    def start(self, record: dict[str, Any]) -> None:
        values = {key: self._encode(key, record.get(key)) for key in COLUMNS}
        values["attempts"] = values["attempts"] or 0
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                f"INSERT INTO commentary_requests ({', '.join(COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in COLUMNS)})",
                [values[key] for key in COLUMNS],
            )

    def update(self, request_id: str, **fields: Any) -> None:
        unknown = set(fields) - set(COLUMNS)
        if unknown:
            raise ValueError(f"unknown commentary log fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                f"UPDATE commentary_requests SET {assignments} WHERE id = ?",
                [*(self._encode(key, value) for key, value in fields.items()), request_id],
            )

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM commentary_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def list(self, *, game_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM commentary_requests"
        parameters: list[Any] = []
        if game_id is not None:
            query += " WHERE game_id = ?"
            parameters.append(game_id)
        query += " ORDER BY seq DESC LIMIT ?"
        parameters.append(limit)
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._decode(row) for row in rows]
