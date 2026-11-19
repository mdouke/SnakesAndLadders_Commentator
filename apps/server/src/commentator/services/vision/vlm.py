"""Read the top face of a die with Claude's vision model (ADR 0006).

The reader takes one JPEG of the ROI and returns a ``VisionReading``. Failures
are raised as ``VlmError`` with ``retryable`` set; the recognition service
decides about retries. The SDK is imported lazily, so the server and the tests
run without it, and the SDK's own retries are switched off.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Any, Literal, Protocol

from ..llm.settings import SettingsFile
from .recognition import READING_MODES, ReadingMode, VisionReading

VLM_PROMPT_VERSION = "dice-vlm-v1"
DEFAULT_VLM_MODEL = "claude-sonnet-5-5"
IMAGE_MAX_SIDE = 768

SYSTEM_PROMPT = "\n".join([
    "You read physical six-sided dice from a camera image of a dice tray.",
    "Report how many dice are in the image and the number of pips on the face",
    "that points straight up toward the camera.",
    "Rules:",
    "- Count only the pips on the top face. Side faces are often visible at the",
    "  edge of the die; never count their pips.",
    "- value is 0 when the top face cannot be read: the die is cut off by the",
    "  image border, leaning against a wall, stacked, blurred or hidden by a hand.",
    "- confidence is high only when exactly one die is fully visible and its top",
    "  face is clear. Use medium or low otherwise.",
    "- diceCount is 0 when no die is visible.",
    "- note is a few words in Japanese explaining what you saw.",
])
USER_PROMPT = "この画像のサイコロの上の面の目を読んでください。"

# Structured output schema (numeric constraints are not supported, so enums).
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "diceCount": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
        "value": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "note": {"type": "string"},
    },
    "required": ["diceCount", "value", "confidence", "note"],
    "additionalProperties": False,
}

_EFFORT_PREFIXES = (
    "claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5",
    "claude-haiku-5", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
    "claude-sonnet-4-6",
)


class VlmError(Exception):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class DiceReader(Protocol):
    name: str

    def read(self, image_jpeg: bytes, *, model: str, timeout: float) -> VisionReading: ...


def parse_output(text: str, *, model: str, duration_ms: int) -> VisionReading:
    """Validate the model's JSON; anything unexpected is an error, not a guess."""
    try:
        data = json.loads(text)
        dice_count = int(data["diceCount"])
        value = int(data["value"])
        confidence = str(data["confidence"])
        note = str(data.get("note", ""))[:200]
    except (ValueError, TypeError, KeyError) as error:
        raise VlmError("画像認識の応答を読めません", code="invalid_output",
                       retryable=False) from error
    if not 0 <= dice_count <= 6 or not 0 <= value <= 6 or confidence not in {
        "high", "medium", "low"
    }:
        raise VlmError("画像認識の応答が範囲外です", code="invalid_output", retryable=False)
    level: Literal["high", "medium", "low"] = confidence  # type: ignore[assignment]
    return VisionReading(
        dice_count=dice_count,
        value=value or None,
        confidence=level,
        note=note,
        model=model,
        duration_ms=duration_ms,
    )


class ClaudeDiceReader:
    """Claude through the official Anthropic SDK; the key is ``ANTHROPIC_API_KEY``."""

    name = "claude"

    def __init__(self, *, client: Any = None) -> None:
        self._client = client
        self._lock = Lock()

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                try:
                    import anthropic
                except ImportError as error:
                    raise VlmError("anthropic パッケージがインストールされていません",
                                   code="unavailable", retryable=False) from error
                self._client = anthropic.Anthropic(max_retries=0)
            return self._client

    def read(self, image_jpeg: bytes, *, model: str, timeout: float) -> VisionReading:
        client = self._get_client()
        import anthropic

        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}
        }
        if model.startswith(_EFFORT_PREFIXES):
            output_config["effort"] = "low"  # counting pips needs little reasoning
        started = monotonic()
        try:
            response = client.with_options(timeout=timeout).messages.create(
                model=model,
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64.standard_b64encode(image_jpeg).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                }],
                output_config=output_config,
            )
        except anthropic.APITimeoutError as error:
            raise VlmError("Claude の応答がタイムアウトしました", code="timeout",
                           retryable=True) from error
        except anthropic.APIConnectionError as error:
            raise VlmError("Claude に接続できませんでした", code="connection",
                           retryable=True) from error
        except anthropic.RateLimitError as error:
            raise VlmError("Claude の利用上限に達しました", code="rate_limited",
                           retryable=True) from error
        except anthropic.AuthenticationError as error:
            raise VlmError("Claude の認証に失敗しました（APIキーを確認してください）",
                           code="auth", retryable=False) from error
        except anthropic.APIStatusError as error:
            raise VlmError(f"Claude がエラーを返しました（HTTP {error.status_code}）",
                           code="status", retryable=error.status_code >= 500) from error
        except anthropic.AnthropicError as error:
            raise VlmError(f"Claude を呼び出せませんでした: {type(error).__name__}",
                           code="error", retryable=False) from error
        duration_ms = round((monotonic() - started) * 1000)
        if response.stop_reason == "refusal":
            raise VlmError("Claude が応答を断りました", code="refused", retryable=False)
        if response.stop_reason == "max_tokens":
            raise VlmError("Claude の応答が途中で切れました", code="truncated",
                           retryable=False)
        text = "".join(block.text for block in response.content if block.type == "text")
        return parse_output(text, model=str(response.model), duration_ms=duration_ms)


class FakeDiceReader:
    """Returns prepared answers (or raises prepared errors) for tests and demos."""

    name = "fake"

    def __init__(self, *answers: VisionReading | VlmError, delay_seconds: float = 0.0) -> None:
        self.answers = list(answers)
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, Any]] = []

    def read(self, image_jpeg: bytes, *, model: str, timeout: float) -> VisionReading:
        self.calls.append({"bytes": len(image_jpeg), "model": model, "timeout": timeout})
        if self.delay_seconds:
            sleep(self.delay_seconds)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, VlmError):
            raise answer
        return replace(answer, model=answer.model or model)


def encode_jpeg(image: Any, *, max_side: int = IMAGE_MAX_SIDE, quality: int = 85) -> bytes:
    """Shrink the ROI so its longer side is at most ``max_side`` and encode it."""
    import cv2

    height, width = image.shape[:2]
    scale = max_side / max(height, width)
    if scale < 1:
        image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise VlmError("画像をJPEGに変換できません", code="encode_failed", retryable=False)
    return bytes(encoded.tobytes())


def vlm_availability() -> dict[str, bool]:
    """What the admin screen shows; never reveals the key."""
    try:
        __import__("anthropic")
        installed = True
    except ImportError:
        installed = False
    return {
        "installed": installed,
        "credentialsConfigured": bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ),
    }


@dataclass(frozen=True, slots=True)
class VlmSettings:
    reading_mode: ReadingMode = "opencv"
    model: str = DEFAULT_VLM_MODEL
    timeout_seconds: float = 8.0
    max_retries: int = 1
    save_images: bool = False

    def validated(self) -> VlmSettings:
        problems: list[str] = []
        if self.reading_mode not in READING_MODES:
            problems.append("readingMode は opencv、vlm、hybrid のいずれかです")
        if not self.model.strip() or len(self.model) > 100:
            problems.append("モデル名を1〜100文字で指定してください")
        if not 2 <= self.timeout_seconds <= 30:
            problems.append("タイムアウトは2〜30秒にしてください")
        if not 0 <= self.max_retries <= 2:
            problems.append("再試行回数は0〜2回にしてください")
        if problems:
            raise ValueError("。".join(problems))
        return replace(self, model=self.model.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "readingMode": self.reading_mode,
            "model": self.model,
            "timeoutSeconds": self.timeout_seconds,
            "maxRetries": self.max_retries,
            "saveImages": self.save_images,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VlmSettings:
        defaults = cls()
        return cls(
            reading_mode=value.get("readingMode", defaults.reading_mode),
            model=str(value.get("model", defaults.model)),
            timeout_seconds=float(value.get("timeoutSeconds", defaults.timeout_seconds)),
            max_retries=int(value.get("maxRetries", defaults.max_retries)),
            save_images=bool(value.get("saveImages", defaults.save_images)),
        ).validated()


class VlmSettingsStore:
    """``var/vlm-settings.json``; ``None`` keeps the settings in memory (tests)."""

    def __init__(self, path: Path | None = None) -> None:
        self._file = SettingsFile(path, VlmSettings(), name="vlm")
        self._lock = Lock()
        stored = self._file.read()
        try:
            self._settings = VlmSettings.from_dict(stored) if stored else VlmSettings()
        except (ValueError, TypeError):
            self._settings = VlmSettings()

    def get(self) -> VlmSettings:
        with self._lock:
            return self._settings

    def update(self, changes: dict[str, Any]) -> VlmSettings:
        with self._lock:
            settings = VlmSettings.from_dict({**self._settings.to_dict(), **changes})
            self._file.write(settings.to_dict())
            self._settings = settings
            return settings
