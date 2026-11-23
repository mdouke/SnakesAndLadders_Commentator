"""Admin-editable commentary settings, stored as JSON next to the database.

API keys are never part of these settings: providers read them from the
environment (or the SDK's own credential store) only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any, Literal

ProviderName = Literal["fake", "claude", "openai"]
Language = Literal["ja", "en"]
Length = Literal["short", "medium", "long"]
Style = Literal["lively", "calm", "kids"]

PROVIDERS: tuple[ProviderName, ...] = ("fake", "claude", "openai")
LANGUAGES: tuple[Language, ...] = ("ja", "en")
LENGTHS: tuple[Length, ...] = ("short", "medium", "long")
STYLES: tuple[Style, ...] = ("lively", "calm", "kids")

# Prompt versions (see prompts/); the newest is the default.
PROMPT_VERSIONS: tuple[str, ...] = (
    "commentary-v1", "commentary-v2", "commentary-v3", "commentary-v4",
    "commentary-v5",
)
LATEST_PROMPT_VERSION = PROMPT_VERSIONS[-1]

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"


@dataclass(frozen=True, slots=True)
class CommentarySettings:
    enabled: bool = True
    provider: ProviderName = "fake"
    claude_model: str = DEFAULT_CLAUDE_MODEL
    openai_model: str = DEFAULT_OPENAI_MODEL
    language: Language = "ja"
    length: Length = "medium"
    style: Style = "lively"
    use_names: bool = True
    # Claude fast mode (faster output at a higher price) where the model has it.
    fast_mode: bool = True
    timeout_seconds: float = 10.0
    max_retries: int = 1
    prompt_version: str = LATEST_PROMPT_VERSION

    @property
    def model(self) -> str:
        if self.provider == "claude":
            return self.claude_model
        if self.provider == "openai":
            return self.openai_model
        return "template"

    def validated(self) -> CommentarySettings:
        problems: list[str] = []
        if self.provider not in PROVIDERS:
            problems.append("provider は fake、claude、openai のいずれかです")
        if self.language not in LANGUAGES:
            problems.append("language は ja または en です")
        if self.length not in LENGTHS:
            problems.append("length は short、medium、long のいずれかです")
        if self.style not in STYLES:
            problems.append("style は lively、calm、kids のいずれかです")
        if not 1 <= self.timeout_seconds <= 60:
            problems.append("タイムアウトは1〜60秒にしてください")
        if not 0 <= self.max_retries <= 3:
            problems.append("再試行回数は0〜3回にしてください")
        if self.prompt_version not in PROMPT_VERSIONS:
            problems.append("プロンプトの版が正しくありません")
        for label, model in (("Claude", self.claude_model), ("OpenAI", self.openai_model)):
            if not model.strip() or len(model) > 100:
                problems.append(f"{label}のモデル名を1〜100文字で指定してください")
        if problems:
            raise ValueError("。".join(problems))
        return replace(
            self, claude_model=self.claude_model.strip(), openai_model=self.openai_model.strip()
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "claudeModel": self.claude_model,
            "openaiModel": self.openai_model,
            "language": self.language,
            "length": self.length,
            "style": self.style,
            "useNames": self.use_names,
            "fastMode": self.fast_mode,
            "timeoutSeconds": self.timeout_seconds,
            "maxRetries": self.max_retries,
            "promptVersion": self.prompt_version,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CommentarySettings:
        defaults = cls()
        return cls(
            enabled=bool(value.get("enabled", defaults.enabled)),
            provider=value.get("provider", defaults.provider),
            claude_model=str(value.get("claudeModel", defaults.claude_model)),
            openai_model=str(value.get("openaiModel", defaults.openai_model)),
            language=value.get("language", defaults.language),
            length=value.get("length", defaults.length),
            style=value.get("style", defaults.style),
            use_names=bool(value.get("useNames", defaults.use_names)),
            fast_mode=bool(value.get("fastMode", defaults.fast_mode)),
            timeout_seconds=float(value.get("timeoutSeconds", defaults.timeout_seconds)),
            max_retries=int(value.get("maxRetries", defaults.max_retries)),
            prompt_version=str(value.get("promptVersion", defaults.prompt_version)),
        ).validated()


class SettingsFile[T]:
    """Small JSON settings file with atomic replace; ``None`` keeps it in memory."""

    def __init__(self, path: Path | None, default: T, *, name: str) -> None:
        self.path = path
        self.default = default
        self.name = name
        self._lock = Lock()

    def read(self) -> dict[str, Any] | None:
        if self.path is None or not self.path.exists():
            return None
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            logging.getLogger("commentator.settings").warning(
                "settings file unreadable; using defaults",
                extra={"event": "settings_unreadable", "settings": self.name},
            )
            return None

    def write(self, value: dict[str, Any]) -> None:
        if self.path is None:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            temporary.replace(self.path)


class CommentarySettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self._file = SettingsFile(path, CommentarySettings(), name="commentary")
        self._lock = Lock()
        stored = self._file.read()
        try:
            self._settings = (
                CommentarySettings.from_dict(stored) if stored else CommentarySettings()
            )
        except (ValueError, TypeError):
            self._settings = CommentarySettings()

    def get(self) -> CommentarySettings:
        with self._lock:
            return self._settings

    def update(self, changes: dict[str, Any]) -> CommentarySettings:
        """Apply camelCase changes; unknown keys are rejected by the API schema."""
        with self._lock:
            merged = {**self._settings.to_dict(), **changes}
            settings = CommentarySettings.from_dict(merged)
            self._file.write(settings.to_dict())
            self._settings = settings
            return settings
