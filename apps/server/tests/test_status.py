from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from commentator.infrastructure.logging import LoggingControl, control
from commentator.infrastructure.persistence import SQLiteGameRepository
from commentator.main import MemoryGameRepository, create_app
from commentator.services.game import GameService
from commentator.services.recognition import RecognitionService

from .test_turn_change import request


def unavailable_recognition(repository: Any) -> RecognitionService:
    return RecognitionService(GameService(repository), pipeline=None, camera_provider=None)


def test_status_reports_each_component(tmp_path: Path) -> None:
    repository = SQLiteGameRepository(tmp_path / "games.sqlite3")
    app = create_app(repository=repository, recognition_service=unavailable_recognition(repository))
    status = request(app, "GET", "/api/status").json()

    assert status["database"]["status"] == "ok"
    assert status["camera"]["status"] == "not_configured"
    assert status["commentary"]["label"].startswith("テンプレート")
    assert status["tts"]["status"] == "ok"  # the silent engine used outside production
    assert status["recording"]["status"] == "not_configured"
    assert status["logging"]["editable"] is False
    assert status["realtimeClients"] == 0


def test_memory_repository_is_flagged_as_not_persistent() -> None:
    repository = MemoryGameRepository()
    app = create_app(repository=repository, recognition_service=unavailable_recognition(repository))
    assert request(app, "GET", "/api/status").json()["database"]["status"] == "warning"


def test_unmanaged_server_refuses_log_setting_change() -> None:
    response = request(create_app(), "PUT", "/api/settings/logging", {"retention": "permanent"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "logging_settings_locked"


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LoggingControl:
    # Reconfiguring the real root logger would break pytest's log capture.
    monkeypatch.setattr(control, "configure_logging", lambda config: None)
    monkeypatch.delenv(control.RETENTION_ENV, raising=False)
    return LoggingControl(
        settings_path=tmp_path / "log-settings.json",
        default_directory=tmp_path / "log",
        managed=True,
    )


def test_retention_change_is_saved_and_reloaded(managed: LoggingControl, tmp_path: Path) -> None:
    app = create_app(logging_control=managed)
    response = request(app, "PUT", "/api/settings/logging", {"retention": "permanent"})
    assert response.status_code == 200
    assert response.json()["retention"] == "permanent"
    assert json.loads((tmp_path / "log-settings.json").read_text()) == {"retention": "permanent"}

    reloaded = LoggingControl(
        settings_path=tmp_path / "log-settings.json",
        default_directory=tmp_path / "log",
        managed=True,
    )
    assert reloaded.config.retention == "permanent"


def test_environment_variable_locks_the_setting(
    managed: LoggingControl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(control.RETENTION_ENV, "rotate")
    assert managed.status()["locked"] is True
    with pytest.raises(PermissionError, match="環境変数"):
        managed.set_retention("permanent")


def test_recent_problems_keep_warnings_and_errors_only() -> None:
    problems = control.RecentProblems(capacity=2)
    logger = logging.getLogger("commentator.test.problems")
    logger.addHandler(problems)
    try:
        logger.info("ignored")
        logger.warning("first", extra={"event": "a"})
        logger.error("second", extra={"event": "b", "errorCode": "x"})
        logger.error("third")
    finally:
        logger.removeHandler(problems)
    items = problems.items()
    assert [item["message"] for item in items] == ["third", "second"]
    assert items[1]["errorCode"] == "x"
