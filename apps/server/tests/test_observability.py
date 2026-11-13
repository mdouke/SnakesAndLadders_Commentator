from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from commentator.infrastructure.logging import (
    JsonFormatter,
    LogConfig,
    apply_retention,
    load_log_config,
)
from commentator.infrastructure.persistence import SQLiteGameRepository
from commentator.main import MemoryGameRepository, create_app
from httpx import ASGITransport, AsyncClient


def test_json_log_is_parseable_and_does_not_include_unapproved_fields() -> None:
    record = logging.LogRecord(
        "commentator.test", logging.INFO, __file__, 1, "safe message", (), None
    )
    record.event = "test_event"
    record.requestId = "request-id"
    record.authorization = "Bearer secret"
    value = json.loads(JsonFormatter().format(record))
    assert value["event"] == "test_event"
    assert value["requestId"] == "request-id"
    assert "secret" not in json.dumps(value)


def test_rotate_removes_expired_logs_but_permanent_keeps_them(tmp_path: Path) -> None:
    old_log = tmp_path / "commentator.jsonl.1"
    old_log.write_text("old", encoding="utf-8")
    now = datetime(2026, 11, 13, tzinfo=UTC)
    old_timestamp = (now - timedelta(days=31)).timestamp()
    os.utime(old_log, (old_timestamp, old_timestamp))

    permanent = LogConfig(retention="permanent", directory=tmp_path)
    assert apply_retention(permanent, now=now) == []
    assert old_log.exists()
    rotate = LogConfig(retention="rotate", directory=tmp_path, retention_days=30)
    assert apply_retention(rotate, now=now) == [old_log]
    assert not old_log.exists()


def test_invalid_log_retention_fails_with_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMENTATOR_LOG_RETENTION", "forever-ish")
    with pytest.raises(ValueError, match="rotate または permanent"):
        load_log_config()


def test_importing_main_does_not_create_production_log_or_database(tmp_path: Path) -> None:
    database_path = tmp_path / "production.sqlite3"
    log_directory = tmp_path / "log"
    environment = os.environ.copy()
    environment["COMMENTATOR_DB_PATH"] = str(database_path)
    environment["COMMENTATOR_LOG_DIR"] = str(log_directory)
    subprocess.run(
        [sys.executable, "-c", "import commentator.main"],
        cwd=tmp_path,
        env=environment,
        check=True,
    )
    assert not database_path.exists()
    assert not log_directory.exists()


def test_request_id_connects_header_error_and_log(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        caplog.set_level(logging.INFO, logger="commentator.api")
        async with AsyncClient(
            transport=ASGITransport(app=create_app(repository=MemoryGameRepository())),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/games/missing")
        assert response.status_code == 404
        request_id = response.headers["X-Request-ID"]
        assert response.json()["detail"] == {
            "code": "game_not_found",
            "message": "ゲームが見つかりません",
            "context": {},
            "requestId": request_id,
        }
        assert any(
            getattr(record, "requestId", None) == request_id for record in caplog.records
        )

    asyncio.run(scenario())


def test_unexpected_error_keeps_request_id_header() -> None:
    async def scenario() -> None:
        application = create_app()

        @application.get("/unexpected-test-error")
        def unexpected_test_error() -> None:
            raise RuntimeError("test-only failure")

        async with AsyncClient(
            transport=ASGITransport(app=application, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/unexpected-test-error")
        assert response.status_code == 500
        assert response.headers["X-Request-ID"] == (
            response.json()["detail"]["requestId"]
        )

    asyncio.run(scenario())


def test_player_count_and_color_validation_use_standard_error() -> None:
    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            seven = await client.post("/api/games", json={"players": [
                {"name": f"P{index}", "color": f"color-{index}"}
                for index in range(7)
            ]})
            duplicate = await client.post("/api/games", json={"players": [
                {"name": "A", "color": "Red"},
                {"name": "B", "color": "red"},
            ]})
        for response in (seven, duplicate):
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "request_validation_failed"
            assert response.headers["X-Request-ID"] == response.json()["detail"]["requestId"]

    asyncio.run(scenario())


def test_sqlite_updated_at_is_utc(tmp_path: Path) -> None:
    repository = SQLiteGameRepository(tmp_path / "utc.sqlite3")
    app = create_app(repository=repository)

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/games", json={"players": [
                {"name": "A", "color": "red"},
                {"name": "B", "color": "blue"},
            ]})
            assert response.status_code == 201

    asyncio.run(scenario())
    with sqlite3.connect(repository.path) as connection:
        value = connection.execute("SELECT updated_at FROM games").fetchone()[0]
    assert datetime.fromisoformat(value).utcoffset() == timedelta(0)
