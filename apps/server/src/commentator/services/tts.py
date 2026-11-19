"""Reading commentary aloud (macOS voices or Google Cloud), with cancellation.

Only the newest comment is ever spoken. When the commentary changes (a newer turn,
undo, correction, regeneration, a different game, the game being aborted), the
speech in progress is stopped and nothing older is left in a queue. Right before
speaking, the service asks the commentary service whether the comment is still the
current one, so a late comment is never read out.

Lock order: game lock -> commentary lock -> speech lock. The speech worker takes the
game and commentary locks only while holding no speech lock.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Event, Thread
from typing import Any, Literal, Protocol

from .llm.settings import SettingsFile

SpeechStatus = Literal["idle", "speaking", "error"]
SpeechProvider = Literal["macos", "google"]
SPEECH_PROVIDERS: tuple[SpeechProvider, ...] = ("macos", "google")
DEFAULT_GOOGLE_VOICE = "ja-JP-Wavenet-B"
SpeechSubscriber = Callable[[dict[str, Any]], None]

PREVIEW_TEXT_JA = "こんにちは。ヘビとハシゴの実況を担当します。よろしくお願いします。"
PREVIEW_TEXT_EN = "Hello! I will be your commentator for Snakes and Ladders."


@dataclass(frozen=True, slots=True)
class Voice:
    name: str
    locale: str
    sample: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "locale": self.locale, "sample": self.sample}


@dataclass(frozen=True, slots=True)
class SpeechSettings:
    enabled: bool = True
    voice: str | None = None  # None: the system default voice
    rate: int | None = None  # words per minute for `say`; None: the voice default
    volume: float = 1.0
    provider: SpeechProvider = "macos"
    google_voice: str = DEFAULT_GOOGLE_VOICE
    google_speaking_rate: float = 1.0  # Google's speakingRate (1.0 = normal)

    def validated(self) -> SpeechSettings:
        if self.provider not in SPEECH_PROVIDERS:
            raise ValueError("読み上げの方法は macos または google です")
        if not self.google_voice.startswith(("ja-", "en-")) or len(self.google_voice) > 100:
            raise ValueError("Google の音声名が正しくありません")
        if not 0.5 <= self.google_speaking_rate <= 2.0:
            raise ValueError("Google の読み上げ速度は0.5〜2.0にしてください")
        if self.rate is not None and not 80 <= self.rate <= 400:
            raise ValueError("読み上げ速度は80〜400にしてください")
        if not 0 <= self.volume <= 1:
            raise ValueError("音量は0〜1にしてください")
        if self.voice is not None and (not self.voice.strip() or len(self.voice) > 100):
            raise ValueError("音声名が正しくありません")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "voice": self.voice, "rate": self.rate,
            "volume": self.volume, "provider": self.provider,
            "googleVoice": self.google_voice, "googleSpeakingRate": self.google_speaking_rate,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SpeechSettings:
        rate = value.get("rate")
        voice = value.get("voice")
        return cls(
            enabled=bool(value.get("enabled", True)),
            voice=str(voice) if voice else None,
            rate=int(rate) if rate is not None else None,
            volume=float(value.get("volume", 1.0)),
            provider=value.get("provider", "macos"),
            google_voice=str(value.get("googleVoice") or DEFAULT_GOOGLE_VOICE),
            google_speaking_rate=float(value.get("googleSpeakingRate", 1.0)),
        ).validated()


class SpeechSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self._file = SettingsFile(path, SpeechSettings(), name="speech")
        stored = self._file.read()
        try:
            self._settings = SpeechSettings.from_dict(stored) if stored else SpeechSettings()
        except (TypeError, ValueError):
            self._settings = SpeechSettings()

    def get(self) -> SpeechSettings:
        return self._settings

    def update(self, changes: dict[str, Any]) -> SpeechSettings:
        settings = SpeechSettings.from_dict({**self._settings.to_dict(), **changes})
        self._file.write(settings.to_dict())
        self._settings = settings
        return settings


class TtsEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def voices(self) -> list[Voice]: ...

    def speak(self, text: str, settings: SpeechSettings, cancel: Event) -> bool:
        """Speak and block until done; return False when cancelled."""
        ...


class SilentTtsEngine:
    """Default outside production: never makes a sound."""

    name = "silent"

    def available(self) -> bool:
        return True

    def voices(self) -> list[Voice]:
        return []

    def speak(self, text: str, settings: SpeechSettings, cancel: Event) -> bool:
        return not cancel.is_set()


_VOICE_LINE = re.compile(
    r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2,3}[_-][A-Za-z0-9]+)\s+#\s?(?P<sample>.*)$"
)


def parse_voices(output: str) -> list[Voice]:
    voices = []
    for line in output.splitlines():
        match = _VOICE_LINE.match(line.rstrip())
        if match:
            voices.append(Voice(match["name"].strip(), match["locale"], match["sample"].strip()))
    return voices


class MacSayEngine:
    """``say`` renders to a temporary file, ``afplay`` plays it at the set volume.

    Rendering first makes the volume setting work for every voice, and both steps
    can be stopped at any moment.
    """

    name = "macos"

    def __init__(self) -> None:
        self._voices: list[Voice] | None = None

    def available(self) -> bool:
        return shutil.which("say") is not None and shutil.which("afplay") is not None

    def voices(self) -> list[Voice]:
        if self._voices is None:
            if not self.available():
                return []
            result = subprocess.run(
                ["say", "-v", "?"], capture_output=True, text=True, timeout=10, check=False
            )
            self._voices = parse_voices(result.stdout)
        return list(self._voices)

    def speak(self, text: str, settings: SpeechSettings, cancel: Event) -> bool:
        with tempfile.TemporaryDirectory(prefix="commentator-tts-") as directory:
            text_path = Path(directory) / "text.txt"
            audio_path = Path(directory) / "speech.aiff"
            text_path.write_text(text, encoding="utf-8")
            render = ["say", "-f", str(text_path), "-o", str(audio_path)]
            if settings.voice:
                render[1:1] = ["-v", settings.voice]
            if settings.rate:
                render[1:1] = ["-r", str(settings.rate)]
            if not _run_cancellable(render, cancel):
                return False
            return _run_cancellable(
                ["afplay", "-v", f"{settings.volume:.2f}", str(audio_path)], cancel
            )


def _run_cancellable(command: list[str], cancel: Event) -> bool:
    if cancel.is_set():
        return False
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    while True:
        try:
            process.wait(timeout=0.05)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                return False
    if process.returncode != 0:
        error = process.stderr.read().strip() if process.stderr else ""
        raise RuntimeError(f"{command[0]} failed ({process.returncode}): {error[:200]}")
    return True


def apply_readings(text: str, readings: dict[str, str]) -> str:
    """Replace display names with their readings, longest name first."""
    for name in sorted(readings, key=len, reverse=True):
        reading = readings[name]
        if name and reading and name != reading:
            text = text.replace(name, reading)
    return text


@dataclass(frozen=True, slots=True)
class _SpeechJob:
    kind: Literal["commentary", "preview"]
    text: str
    request_id: str | None = None
    turn_id: str | None = None
    settings: SpeechSettings | None = None  # preview may try unsaved settings


class SpeechService:
    def __init__(
        self,
        *,
        engine: TtsEngine | None = None,
        engines: dict[str, TtsEngine] | None = None,
        settings: SpeechSettingsStore | None = None,
        is_current: Callable[[str | None], bool] = lambda _request_id: False,
        readings: Callable[[], dict[str, str]] = dict,
        language: Callable[[], str] = lambda: "ja",
    ) -> None:
        # ``engine`` serves every provider (tests); ``engines`` maps provider names.
        self._default_engine: TtsEngine = engine or SilentTtsEngine()
        self.engines: dict[str, TtsEngine] = dict(engines or {})
        self.settings = settings or SpeechSettingsStore()
        self.is_current = is_current
        self.readings = readings
        self.language = language
        self._condition = Condition()
        self._pending: _SpeechJob | None = None
        self._cancel = Event()
        self._speaking: _SpeechJob | None = None
        self._state: dict[str, Any] = _idle_speech()
        self._subscribers: list[SpeechSubscriber] = []
        self._stopped = False
        self._worker: Thread | None = None

    def engine_for(self, provider: str) -> TtsEngine:
        return self.engines.get(provider, self._default_engine)

    @property
    def engine(self) -> TtsEngine:
        """The engine of the selected provider."""
        return self.engine_for(self.settings.get().provider)

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        with self._condition:
            if self._worker is not None:
                return
            self._stopped = False
            self._worker = Thread(target=self._run, name="speech", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._pending = None
            self._cancel.set()
            self._condition.notify_all()
            worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=3)

    # -- subscriptions ---------------------------------------------------------

    def subscribe(self, callback: SpeechSubscriber) -> None:
        with self._condition:
            self._subscribers.append(callback)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return dict(self._state)

    def send_snapshot(self, callback: SpeechSubscriber) -> None:
        with self._condition:
            callback(dict(self._state))

    # -- inputs ----------------------------------------------------------------

    def commentary_changed(self, state: dict[str, Any]) -> None:
        """Commentary subscriber; may run under the game and commentary locks."""
        with self._condition:
            speaking = self._speaking
            if speaking is not None and speaking.kind == "commentary" and (
                speaking.request_id != state.get("requestId") or state["status"] != "ready"
            ):
                self._cancel.set()  # the comment being spoken is no longer current
            if self._pending is not None and self._pending.kind == "commentary":
                self._pending = None
            if state["status"] == "ready" and state.get("text") and self.settings.get().enabled:
                if speaking is None or speaking.request_id != state["requestId"]:
                    self._pending = _SpeechJob(
                        kind="commentary", text=str(state["text"]),
                        request_id=state["requestId"], turn_id=state.get("turnId"),
                    )
            self._condition.notify_all()

    def preview(self, text: str | None = None, settings: SpeechSettings | None = None) -> None:
        if not self.engine.available():
            raise RuntimeError("このコンピューターでは読み上げを利用できません")
        sample = text.strip() if text and text.strip() else (
            PREVIEW_TEXT_EN if self.language() == "en" else PREVIEW_TEXT_JA
        )
        with self._condition:
            if self._speaking is not None:
                self._cancel.set()
            self._pending = _SpeechJob(kind="preview", text=sample[:300], settings=settings)
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self._pending = None
            if self._speaking is not None:
                self._cancel.set()
            self._condition.notify_all()

    def settings_changed(self) -> None:
        if not self.settings.get().enabled:
            self.cancel()

    # -- worker ----------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._stopped:
                    self._condition.wait()
                if self._stopped:
                    return
                job, self._pending = self._pending, None
            assert job is not None
            self._speak(job)

    def _speak(self, job: _SpeechJob) -> None:
        logger = logging.getLogger("commentator.speech")
        text = job.text
        if job.kind == "commentary":
            # Taken without the speech lock (lock order: game -> commentary -> speech).
            if not self.is_current(job.request_id):
                logger.info("stale speech skipped",
                            extra={"event": "speech_skipped", "requestId": job.request_id})
                return
            text = apply_readings(text, self.readings())
        settings = job.settings or self.settings.get()
        with self._condition:
            if self._pending is not None and self._pending.kind == "commentary":
                return  # a newer comment arrived meanwhile; speak that one instead
            self._cancel = Event()
            cancel = self._cancel
            self._speaking = job
            self._set_state({
                "status": "speaking", "kind": job.kind, "requestId": job.request_id,
                "turnId": job.turn_id, "text": job.text, "error": None,
            })
        error: str | None = None
        completed = False
        engine = self.engine_for(settings.provider)
        try:
            completed = engine.speak(text, settings, cancel)
        except Exception as caught:
            error = str(caught)[:300] or type(caught).__name__
            logger.error("speech failed", exc_info=caught,
                         extra={"event": "speech_failed", "errorCode": "speech_failed",
                                "requestId": job.request_id, "provider": settings.provider,
                                "reason": error})
            fallback = self.engine_for("macos")
            if (
                settings.provider != "macos" and fallback is not engine
                and fallback.available() and not cancel.is_set()
            ):
                # The game goes on: read this comment with the macOS voice instead.
                try:
                    completed = fallback.speak(text, settings, cancel)
                    logger.warning("speech fell back to macOS",
                                   extra={"event": "speech_fallback",
                                          "requestId": job.request_id})
                    error = None
                except Exception as second:
                    logger.error("fallback speech failed", exc_info=second,
                                 extra={"event": "speech_failed", "errorCode": "speech_failed"})
        logger.info(
            "speech finished" if completed else "speech stopped",
            extra={"event": "speech_finished" if completed else "speech_cancelled",
                   "requestId": job.request_id, "kind": job.kind},
        )
        with self._condition:
            self._speaking = None
            if error is not None:
                self._set_state({**_idle_speech(), "status": "error", "kind": job.kind,
                                 "requestId": job.request_id, "turnId": job.turn_id,
                                 "error": error})
            else:
                self._set_state(_idle_speech())

    def _set_state(self, state: dict[str, Any]) -> None:
        self._state = state
        for subscriber in tuple(self._subscribers):
            try:
                subscriber(dict(state))
            except Exception:
                logging.getLogger("commentator.speech").exception(
                    "speech subscriber failed", extra={"event": "speech_subscriber_failed"}
                )


def _idle_speech() -> dict[str, Any]:
    return {
        "status": "idle", "kind": None, "requestId": None, "turnId": None, "text": None,
        "error": None,
    }
