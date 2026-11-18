from __future__ import annotations

import logging
from pathlib import Path

import pytest
from commentator.infrastructure.secrets import load_secrets, parse_env_file


def test_parse_env_file_accepts_comments_export_and_quotes() -> None:
    text = """
# Claude
ANTHROPIC_API_KEY=sk-ant-abc
export OPENAI_API_KEY="sk-proj-xyz"
EMPTY=
SINGLE='a b'  
TRAILING=value # comment
"""
    assert parse_env_file(text) == {
        "ANTHROPIC_API_KEY": "sk-ant-abc",
        "OPENAI_API_KEY": "sk-proj-xyz",
        "EMPTY": "",
        "SINGLE": "a b",
        "TRAILING": "value",
    }


def test_parse_env_file_rejects_broken_lines() -> None:
    with pytest.raises(ValueError, match="2行目"):
        parse_env_file("A=1\nnot a pair\n")


def test_load_secrets_keeps_explicit_environment_and_never_logs_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "secrets.env"
    path.write_text("ANTHROPIC_API_KEY=sk-file\nOPENAI_API_KEY=sk-openai\nBLANK=\n")
    path.chmod(0o600)
    environ = {"OPENAI_API_KEY": "sk-terminal"}
    with caplog.at_level(logging.INFO):
        loaded = load_secrets(path, environ)

    assert loaded == ["ANTHROPIC_API_KEY"]
    assert environ == {"OPENAI_API_KEY": "sk-terminal", "ANTHROPIC_API_KEY": "sk-file"}
    assert "sk-file" not in caplog.text and "sk-openai" not in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_missing_file_is_fine_and_loose_permissions_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert load_secrets(tmp_path / "none.env", {}) == []
    path = tmp_path / "secrets.env"
    path.write_text("ANTHROPIC_API_KEY=sk-file\n")
    path.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        load_secrets(path, {})
    assert any(getattr(r, "event", None) == "secrets_permissions" for r in caplog.records)


def test_unreadable_file_is_reported_without_crashing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "secrets.env"
    path.write_text("this line is broken\n")
    environ: dict[str, str] = {}
    assert load_secrets(path, environ) == []
    assert any(getattr(r, "event", None) == "secrets_unreadable" for r in caplog.records)
