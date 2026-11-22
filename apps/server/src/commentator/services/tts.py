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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Condition, Event, Thread
from time import monotonic
from typing import Any, Literal, Protocol

from .llm.settings import SettingsFile

SpeechStatus = Literal["idle", "speaking", "error"]
SpeechProvider = Literal["macos", "google", "gemini"]
SPEECH_PROVIDERS: tuple[SpeechProvider, ...] = ("macos", "google", "gemini")
DEFAULT_GOOGLE_VOICE = "ja-JP-Wavenet-B"
# Gemini TTS: a style text steers the delivery. Models differ per backend:
# "cloud" = Cloud Text-to-Speech (service account), "api" = Gemini API (API key).
GeminiBackend = Literal["cloud", "api"]
GEMINI_BACKENDS: tuple[GeminiBackend, ...] = ("cloud", "api")
GEMINI_TTS_MODELS: dict[str, tuple[str, ...]] = {
    # Measured 2026-11-19: flash-lite 2.3-3.0 s per sentence; flash 2.6-8.3 s and
    # sometimes over 20 s. The first entry is the default.
    "cloud": (
        "gemini-2.5-flash-lite-preview-tts", "gemini-2.5-flash-tts", "gemini-2.5-pro-tts",
        "gemini-3.1-flash-tts-preview",
    ),
    "api": ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"),
}
# When one provider fails, try these next (and finally the macOS voice).
FALLBACK_PROVIDERS: dict[str, tuple[str, ...]] = {
    "gemini": ("google", "macos"), "google": ("macos",), "macos": (),
}
GEMINI_TTS_VOICES: dict[str, str] = {
    "Achernar": "女性", "Achird": "男性", "Algenib": "男性", "Algieba": "男性",
    "Alnilam": "男性", "Aoede": "女性", "Autonoe": "女性", "Callirrhoe": "女性",
    "Charon": "男性", "Despina": "女性", "Enceladus": "男性", "Erinome": "女性",
    "Fenrir": "男性", "Gacrux": "女性", "Iapetus": "男性", "Kore": "女性",
    "Laomedeia": "女性", "Leda": "女性", "Orus": "男性", "Pulcherrima": "女性",
    "Puck": "男性", "Rasalgethi": "男性", "Sadachbia": "男性", "Sadaltager": "男性",
    "Schedar": "男性", "Sulafat": "女性", "Umbriel": "男性", "Vindemiatrix": "女性",
    "Zephyr": "女性", "Zubenelgenubi": "男性",
}
DEFAULT_GEMINI_STYLE = "明るく元気なボードゲームの実況アナウンサーのように、テンポよく"
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
    gemini_backend: GeminiBackend = "cloud"
    gemini_model: str = GEMINI_TTS_MODELS["cloud"][0]
    gemini_voice: str = "Kore"
    gemini_style: str = DEFAULT_GEMINI_STYLE  # how to read, in plain words

    def validated(self) -> SpeechSettings:
        if self.provider not in SPEECH_PROVIDERS:
            raise ValueError("読み上げの方法は macos、google、gemini のいずれかです")
        if self.gemini_backend not in GEMINI_BACKENDS:
            raise ValueError("Gemini の接続先は cloud または api です")
        if not self.gemini_model.startswith("gemini-") or len(self.gemini_model) > 100:
            raise ValueError("Gemini のモデル名が正しくありません")
        if self.gemini_voice not in GEMINI_TTS_VOICES:
            raise ValueError("Gemini の音声名が正しくありません")
        if len(self.gemini_style) > 500:
            raise ValueError("読み方の指示は500文字以内にしてください")
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
            "geminiBackend": self.gemini_backend,
            "geminiModel": self.gemini_model, "geminiVoice": self.gemini_voice,
            "geminiStyle": self.gemini_style,
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
            gemini_backend=value.get("geminiBackend", "cloud"),
            gemini_model=str(value.get("geminiModel") or GEMINI_TTS_MODELS["cloud"][0]),
            gemini_voice=str(value.get("geminiVoice") or "Kore"),
            gemini_style=str(value.get("geminiStyle", DEFAULT_GEMINI_STYLE)).strip(),
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


class SpeechProgress:
    """How far an engine got with a text, so the next voice can go on from there.

    Engines call ``audio_started()`` right before playing audio and
    ``spoken_through(end)`` after the audio of ``text[:end]`` finished playing.
    Only the speech worker thread touches it.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.first_audio_at: float | None = None  # monotonic time
        self._spoken_end = 0

    def audio_started(self) -> None:
        if self.first_audio_at is None:
            self.first_audio_at = monotonic()

    def spoken_through(self, end: int) -> None:
        self._spoken_end = max(self._spoken_end, min(end, len(self.text)))

    @property
    def remaining(self) -> str:
        """The part not read yet (the whole text when nothing was finished)."""
        return self.text[self._spoken_end:].strip()


class TtsEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def voices(self) -> list[Voice]: ...

    def speak(
        self, text: str, settings: SpeechSettings, cancel: Event,
        progress: SpeechProgress | None = None,
    ) -> bool:
        """Speak and block until done; return False when cancelled.

        ``progress`` (optional) is told when audio starts and how far it got.
        """
        ...


class SilentTtsEngine:
    """Default outside production: never makes a sound."""

    name = "silent"

    def available(self) -> bool:
        return True

    def voices(self) -> list[Voice]:
        return []

    def speak(
        self, text: str, settings: SpeechSettings, cancel: Event,
        progress: SpeechProgress | None = None,
    ) -> bool:
        if cancel.is_set():
            return False
        if progress is not None:
            progress.audio_started()
            progress.spoken_through(len(text))
        return True


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


MACOS_LOOKAHEAD = 2  # sentences rendered ahead of the one playing


class MacSayEngine:
    """``say`` renders each sentence to a file, ``afplay`` plays it at the set volume.

    Rendering first makes the volume setting work for every voice, and both steps
    can be stopped at any moment. Like the Google engines, the comment is split
    into sentences: the first sentence plays while the next ones are rendered, so
    speech starts after one sentence instead of the whole comment.
    """

    name = "macos"

    def __init__(
        self,
        *,
        render: Callable[[str, SpeechSettings, Event], bytes] | None = None,
        player: Callable[[Path, float, Event], bool] | None = None,
    ) -> None:
        self._voices: list[Voice] | None = None
        self._render = render or _render_with_say
        self._player = player or _play_with_afplay
        self._injected = render is not None
        # `say` takes about as long to render a sentence as it takes to play it,
        # so two sentences are rendered ahead to avoid gaps between them.
        self._pool = ThreadPoolExecutor(
            max_workers=MACOS_LOOKAHEAD, thread_name_prefix="macos-tts"
        )

    def available(self) -> bool:
        if self._injected:
            return True
        return shutil.which("say") is not None and shutil.which("afplay") is not None

    def voices(self) -> list[Voice]:
        if self._voices is None:
            if not self.available() or self._injected:
                return []
            result = subprocess.run(
                ["say", "-v", "?"], capture_output=True, text=True, timeout=10, check=False
            )
            self._voices = parse_voices(result.stdout)
        return list(self._voices)

    def speak(
        self, text: str, settings: SpeechSettings, cancel: Event,
        progress: SpeechProgress | None = None,
    ) -> bool:
        from .google_tts import speak_sentences

        return speak_sentences(
            text, lambda sentence: self._render(sentence, settings, cancel), suffix=".aiff",
            pool=self._pool, player=self._player, volume=settings.volume, cancel=cancel,
            progress=progress, lookahead=MACOS_LOOKAHEAD,
        )


def _render_with_say(text: str, settings: SpeechSettings, cancel: Event) -> bytes:
    """One sentence to AIFF audio; empty when cancelled."""
    with tempfile.TemporaryDirectory(prefix="commentator-say-") as directory:
        text_path = Path(directory) / "text.txt"
        audio_path = Path(directory) / "speech.aiff"
        text_path.write_text(text, encoding="utf-8")
        render = ["say", "-f", str(text_path), "-o", str(audio_path)]
        if settings.voice:
            render[1:1] = ["-v", settings.voice]
        if settings.rate:
            render[1:1] = ["-r", str(settings.rate)]
        if not _run_cancellable(render, cancel):
            return b""
        return audio_path.read_bytes()


def _play_with_afplay(path: Path, volume: float, cancel: Event) -> bool:
    return _run_cancellable(["afplay", "-v", f"{volume:.2f}", str(path)], cancel)


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


def voice_label(provider: str, settings: SpeechSettings) -> str:
    """The voice a provider reads with, for the log (never user text)."""
    if provider == "google":
        return settings.google_voice
    if provider == "gemini":
        models = GEMINI_TTS_MODELS.get(settings.gemini_backend, ())
        model = settings.gemini_model if settings.gemini_model in models else (
            models[0] if models else settings.gemini_model
        )
        return f"{model}/{settings.gemini_voice}"
    if provider == "macos":
        return settings.voice or "default"
    return provider


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
    # When the comment became ready (or the preview was asked for); monotonic time.
    created_at: float = field(default_factory=monotonic)


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
        self._cooldown_until: dict[str, float] = {}  # provider -> monotonic time

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
        tried: list[TtsEngine] = []
        remaining = text  # what is still to be read; a fallback voice goes on from here
        first_audio_at: float | None = None
        spoken_by: list[str] = []  # provider of each voice that played audio
        voices: list[str] = []
        for provider in (settings.provider, *FALLBACK_PROVIDERS.get(settings.provider, ())):
            engine = self.engine_for(provider)
            if engine in tried or cancel.is_set():
                continue
            if provider != settings.provider and (
                provider not in self.engines or not engine.available()
            ):
                continue
            waiting = self._cooldown_until.get(provider, 0.0) - monotonic()
            if waiting > 0:
                logger.info("speech provider cooling down",
                            extra={"event": "speech_provider_skipped", "provider": provider,
                                   "reason": f"retry in {waiting:.0f}s"})
                continue
            tried.append(engine)
            progress = SpeechProgress(remaining)
            try:
                completed = engine.speak(remaining, settings, cancel, progress)
                if provider != settings.provider:
                    # The game goes on with the next voice; the comment is still read.
                    logger.warning("speech fell back", extra={
                        "event": "speech_fallback", "requestId": job.request_id,
                        "provider": provider, "voice": voice_label(provider, settings),
                        "reason": "resumed mid-comment" if remaining != text else None,
                    })
                error = None
                break
            except Exception as caught:
                error = str(caught)[:300] or type(caught).__name__
                retry_after = getattr(caught, "retry_after", None)
                if retry_after:
                    # Rate limited: skip this provider until Google allows it again.
                    self._cooldown_until[provider] = monotonic() + float(retry_after)
                logger.error("speech failed", exc_info=caught,
                             extra={"event": "speech_failed", "errorCode": "speech_failed",
                                    "requestId": job.request_id, "provider": provider,
                                    "voice": voice_label(provider, settings),
                                    "reason": error})
            finally:
                if progress.first_audio_at is not None:
                    if first_audio_at is None:
                        first_audio_at = progress.first_audio_at
                    spoken_by.append(provider)
                    voices.append(voice_label(provider, settings))
                remaining = progress.remaining
            if not remaining:
                # Failed after the last sentence was played: nothing is left to read.
                completed, error = not cancel.is_set(), None
                break
        finished_at = monotonic()
        logger.info(
            "speech finished" if completed else "speech stopped",
            extra={"event": "speech_finished" if completed else "speech_cancelled",
                   "requestId": job.request_id, "kind": job.kind,
                   # e.g. "gemini>google" when WaveNet read the rest of the comment
                   "provider": ">".join(spoken_by) or None,
                   "voice": ">".join(voices) or None,
                   "firstAudioMs": (round((first_audio_at - job.created_at) * 1000)
                                    if first_audio_at is not None else None),
                   "durationMs": round((finished_at - job.created_at) * 1000)},
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
