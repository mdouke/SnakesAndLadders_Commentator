"""Reading commentary aloud with Google: Cloud Text-to-Speech and Gemini TTS.

- ``GoogleTtsEngine``: Cloud Text-to-Speech voices (WaveNet, Neural2, Chirp 3
  HD) with ``GOOGLE_TTS_API_KEY``.
- ``GeminiTtsEngine``: Gemini TTS, steered by a natural-language ``style``, through
  either backend:
  - ``cloud``: Cloud Text-to-Speech with a service account key file
    (``GOOGLE_APPLICATION_CREDENTIALS``). API keys are rejected here because the
    Vertex AI model needs an authenticated principal. 150 requests per minute
    by default and no daily cap.
  - ``api``: the Gemini API with ``GEMINI_API_KEY`` (only about 100 requests per
    day, even on the paid tier).

Keys come from ``var/secrets.env``, are sent in a header and are never logged.

To start speaking quickly, the text is split into sentences (``speak_sentences``,
also used by the macOS engine): the first
sentence is synthesized and played while the next one is synthesized in the
background. Audio is played with ``afplay`` at the configured volume, so a
change of comment stops it at once, exactly like the macOS engine.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import ssl
import tempfile
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .tts import SpeechProgress, SpeechSettings, Voice

API_ROOT = "https://texttospeech.googleapis.com/v1"
KEY_ENV = "GOOGLE_TTS_API_KEY"
REQUEST_TIMEOUT_SECONDS = 10.0
GEMINI_KEY_ENV = "GEMINI_API_KEY"
GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
# Gemini usually answers in 2-3 s; past this the next voice (WaveNet) is faster.
GEMINI_TIMEOUT_SECONDS = 8.0
CREDENTIALS_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
# services -> commentator -> src -> server -> apps -> repository root
REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
_CACHE_SIZE = 64
_RETRY_IN = re.compile(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.IGNORECASE)
_SENTENCE = re.compile(r"[^。！？!?]+[。！？!?]*")
_MIN_SENTENCE = 8  # merge tiny pieces ("4！") into the next sentence


class GoogleTtsError(RuntimeError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after  # seconds Google asked us to wait (rate limits)


def _retry_after(detail: str, header: str | None) -> float | None:
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = _RETRY_IN.search(detail)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (float(value) if value else 0.0 for value in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _error_detail(raw: str) -> str:
    """Google returns ``{"error": ...}`` or ``[{"error": ...}]``."""
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if not isinstance(error, dict):
        return ""
    message = str(error.get("message", ""))
    for item in error.get("details") or []:
        if isinstance(item, dict) and item.get("reason") == "ACCESS_DENIED":
            service = (item.get("metadata") or {}).get("service", "")
            message += f" (ACCESS_DENIED: {service})" if service else " (ACCESS_DENIED)"
    return message


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Where each sentence is in ``text``: split after 。！？, tiny pieces merged."""
    pieces: list[tuple[int, int]] = []
    for match in _SENTENCE.finditer(text):
        piece = match.group()
        stripped = piece.strip()
        if stripped:
            start = match.start() + piece.index(stripped)
            pieces.append((start, start + len(stripped)))
    merged: list[tuple[int, int]] = []
    for start, end in pieces:
        if merged and merged[-1][1] - merged[-1][0] < _MIN_SENTENCE:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    if len(merged) > 1 and merged[-1][1] - merged[-1][0] < _MIN_SENTENCE:
        last = merged.pop()
        merged[-1] = (merged[-1][0], last[1])
    if not merged and text.strip():
        start = text.index(text.strip())
        merged.append((start, start + len(text.strip())))
    return merged


def split_sentences(text: str) -> list[str]:
    """Split after 。！？ and merge pieces too short to be worth a request."""
    return [text[start:end] for start, end in sentence_spans(text)]


Transport = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


def _ssl_context() -> ssl.SSLContext:
    """Verify HTTPS with certifi's CA bundle when it is installed.

    The python.org build of Python on macOS ships without CA certificates
    unless "Install Certificates.command" was run, so the default context
    rejects every server. certifi comes with the llm extra (anthropic/httpx).
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _http(
    method: str, url: str, body: dict[str, Any] | None, timeout: float, *,
    key_env: str = KEY_ENV, service: str = "Google の読み上げ",
    bearer: Callable[[], str] | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer()}"
    else:
        headers["X-Goog-Api-Key"] = os.environ.get(key_env, "")
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as error:
        try:
            detail = _error_detail(error.read().decode("utf-8", "replace"))
        except Exception:
            detail = ""
        credential = (
            "サービスアカウントの鍵ファイル" if bearer is not None else f"{key_env} の APIキー"
        )
        if "ACCESS_DENIED" in detail and "generativelanguage" in detail:
            message = (f"{service}を使えません（{key_env} が Gemini API に使えないキーです。"
                       "Google AI Studio で作ったキーか確認してください）")
        elif error.code in {401, 403} or (error.code == 400 and "API key" in detail):
            message = (f"{service}を使えません（{credential}と、"
                       "APIの有効化・権限を確認してください）")
        elif error.code == 429:
            message = f"{service}の利用上限に達しました"
        else:
            message = f"{service}がエラーを返しました（HTTP {error.code}）"
        raise GoogleTtsError(
            f"{message}: {detail[:240]}" if detail else message,
            retry_after=(
                _retry_after(detail, error.headers.get("Retry-After") if error.headers else None)
                if error.code == 429 else None
            ),
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise GoogleTtsError(
                f"{service}に接続できませんでした（HTTPS の証明書を確認できません）"
            ) from error
        raise GoogleTtsError(
            f"{service}に接続できませんでした（{type(reason).__name__}）"
        ) from error


class GoogleTtsEngine:
    name = "google"

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        player: Callable[[Path, float, Event], bool] | None = None,
        key_present: Callable[[], bool] | None = None,
    ) -> None:
        self._transport = transport or _http
        self._player = player or _play_with_afplay
        self._key_present = key_present or (lambda: bool(os.environ.get(KEY_ENV)))
        self._voices: list[Voice] | None = None
        self._cache: OrderedDict[tuple[str, float, str], bytes] = OrderedDict()
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="google-tts")

    def key_configured(self) -> bool:
        return self._key_present()

    def available(self) -> bool:
        return self._key_present() and (
            self._player is not _play_with_afplay or shutil.which("afplay") is not None
        )

    def voices(self) -> list[Voice]:
        from .tts import Voice

        if self._voices is None:
            if not self._key_present():
                return []
            try:
                data = self._transport("GET", f"{API_ROOT}/voices?languageCode=ja-JP", None,
                                       REQUEST_TIMEOUT_SECONDS)
            except GoogleTtsError as error:
                logging.getLogger("commentator.speech").warning(
                    "google voices unavailable",
                    extra={"event": "google_voices_failed", "reason": str(error)},
                )
                return []
            voices = []
            for item in data.get("voices", []):
                name = str(item.get("name", ""))
                if not name.startswith("ja-JP-"):
                    continue
                gender = {"FEMALE": "女性", "MALE": "男性"}.get(item.get("ssmlGender"), "")
                kind = name.split("-")[2] if name.count("-") >= 3 else ""
                voices.append(Voice(name, "ja-JP", f"{kind} {gender}".strip()))
            self._voices = sorted(voices, key=lambda voice: voice.name)
        return list(self._voices)

    def synthesize(self, text: str, voice: str, speaking_rate: float) -> bytes:
        key = (voice, speaking_rate, text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        language = "-".join(voice.split("-")[:2]) if voice.count("-") >= 2 else "ja-JP"
        data = self._transport("POST", f"{API_ROOT}/text:synthesize", {
            "input": {"text": text},
            "voice": {"languageCode": language, "name": voice},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": speaking_rate},
        }, REQUEST_TIMEOUT_SECONDS)
        try:
            audio = base64.b64decode(data["audioContent"])
        except (KeyError, ValueError, TypeError) as error:
            raise GoogleTtsError("Google の読み上げの応答を読めません") from error
        with self._lock:
            self._cache[key] = audio
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        return audio

    def speak(
        self, text: str, settings: SpeechSettings, cancel: Event,
        progress: SpeechProgress | None = None,
    ) -> bool:
        voice = settings.google_voice
        rate = settings.google_speaking_rate
        return speak_sentences(
            text, lambda sentence: self.synthesize(sentence, voice, rate), suffix=".mp3",
            pool=self._pool, player=self._player, volume=settings.volume, cancel=cancel,
            progress=progress,
        )


def speak_sentences(
    text: str,
    fetch: Callable[[str], bytes],
    *,
    suffix: str,
    pool: ThreadPoolExecutor,
    player: Callable[[Path, float, Event], bool],
    volume: float,
    cancel: Event,
    progress: SpeechProgress | None = None,
    lookahead: int = 1,
) -> bool:
    """Play sentence by sentence while the next sentences are being synthesized.

    ``lookahead`` sentences are synthesized ahead of the one playing (the pool
    needs as many workers to make them in parallel). ``progress`` learns after
    each sentence how far the text was read, so when a later sentence fails the
    next voice reads only the rest.
    """
    spans = sentence_spans(text)
    if not spans:
        return not cancel.is_set()
    sentences = [text[start:end] for start, end in spans]
    pending: dict[int, Future[bytes]] = {
        index: pool.submit(fetch, sentences[index])
        for index in range(min(lookahead, len(sentences)))
    }
    try:
        with tempfile.TemporaryDirectory(prefix="commentator-tts-") as directory:
            for index in range(len(sentences)):
                audio = pending.pop(index).result()  # raises on failure
                if cancel.is_set():
                    return False
                ahead = index + lookahead
                if ahead < len(sentences):
                    pending[ahead] = pool.submit(fetch, sentences[ahead])
                path = Path(directory) / f"part-{index}{suffix}"
                path.write_bytes(audio)
                if progress is not None:
                    progress.audio_started()
                if not player(path, volume, cancel):
                    return False
                if progress is not None:
                    progress.spoken_through(spans[index][1])
    finally:
        for future in pending.values():
            future.cancel()  # not started yet: no need to make it any more
    return not cancel.is_set()
    sentences = [text[start:end] for start, end in spans]
    upcoming: Future[bytes] = pool.submit(fetch, sentences[0])
    with tempfile.TemporaryDirectory(prefix="commentator-tts-") as directory:
        for index in range(len(sentences)):
            audio = upcoming.result()  # raises GoogleTtsError on failure
            if cancel.is_set():
                return False
            if index + 1 < len(sentences):
                upcoming = pool.submit(fetch, sentences[index + 1])
            path = Path(directory) / f"part-{index}{suffix}"
            path.write_bytes(audio)
            if progress is not None:
                progress.audio_started()
            if not player(path, volume, cancel):
                return False
            if progress is not None:
                progress.spoken_through(spans[index][1])
    return not cancel.is_set()


def credentials_path() -> Path | None:
    """The service account key file; a relative path is taken from the repository."""
    configured = os.environ.get(CREDENTIALS_ENV, "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return path if path.is_file() else None


class ServiceAccountToken:
    """OAuth access tokens from a service account key file (refreshed when expired)."""

    SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)

    def __init__(self, path: Callable[[], Path | None] = credentials_path) -> None:
        self._path = path
        self._lock = Lock()
        self._credentials: Any = None
        self._loaded_from: Path | None = None

    def configured(self) -> bool:
        if self._path() is None:
            return False
        try:
            import google.auth.transport.requests  # noqa: F401
            import google.oauth2.service_account  # noqa: F401
        except ImportError:
            return False
        return True

    def __call__(self) -> str:
        path = self._path()
        if path is None:
            raise GoogleTtsError(
                f"サービスアカウントの鍵ファイルが見つかりません（{CREDENTIALS_ENV}）"
            )
        with self._lock:
            try:
                from google.auth.transport.requests import Request
                from google.oauth2 import service_account

                if self._credentials is None or self._loaded_from != path:
                    self._credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                        str(path), scopes=list(self.SCOPES)
                    )
                    self._loaded_from = path
                if not self._credentials.valid:
                    self._credentials.refresh(Request())
                return str(self._credentials.token)
            except ImportError as error:
                raise GoogleTtsError(
                    "google-auth パッケージがインストールされていません"
                ) from error
            except GoogleTtsError:
                raise
            except Exception as error:
                # The message names the problem only; the key file content is never logged.
                raise GoogleTtsError(
                    f"サービスアカウントで認証できませんでした（{type(error).__name__}）"
                ) from error


class GeminiTtsEngine:
    """Gemini TTS steered by a style text, through Cloud TTS or the Gemini API."""

    name = "gemini"

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        cloud_transport: Transport | None = None,
        player: Callable[[Path, float, Event], bool] | None = None,
        key_present: Callable[[], bool] | None = None,
        token: ServiceAccountToken | None = None,
        cloud_present: Callable[[], bool] | None = None,
    ) -> None:
        self._token = token or ServiceAccountToken()
        self._transport = transport or (
            lambda method, url, body, timeout: _http(
                method, url, body, timeout, key_env=GEMINI_KEY_ENV, service="Gemini の読み上げ"
            )
        )
        self._cloud_transport = cloud_transport or (
            lambda method, url, body, timeout: _http(
                method, url, body, timeout, service="Gemini の読み上げ（Google Cloud）",
                bearer=self._token,
            )
        )
        self._player = player or _play_with_afplay
        self._key_present = key_present or (lambda: bool(os.environ.get(GEMINI_KEY_ENV)))
        self._cloud_present = cloud_present or self._token.configured
        self._cache: OrderedDict[tuple[str, ...], bytes] = OrderedDict()
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gemini-tts")

    def key_configured(self) -> bool:
        return self._key_present()

    def cloud_configured(self) -> bool:
        return self._cloud_present()

    def available(self) -> bool:
        return (self._key_present() or self._cloud_present()) and (
            self._player is not _play_with_afplay or shutil.which("afplay") is not None
        )

    def voices(self) -> list[Voice]:
        from .tts import GEMINI_TTS_VOICES, Voice

        return [Voice(name, "multi", gender) for name, gender in GEMINI_TTS_VOICES.items()]

    def synthesize(
        self, text: str, *, model: str, voice: str, style: str, backend: str = "api",
    ) -> bytes:
        """One sentence to audio: MP3 from Cloud TTS, WAV (24 kHz mono) from the API."""
        key = (backend, model, voice, style, text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        if backend == "cloud":
            data = self._cloud_transport("POST", f"{API_ROOT}/text:synthesize", {
                "input": {"text": text, **({"prompt": style} if style else {})},
                "voice": {"languageCode": "ja-JP", "name": voice, "model_name": model},
                "audioConfig": {"audioEncoding": "MP3"},
            }, GEMINI_TIMEOUT_SECONDS)
            try:
                audio = base64.b64decode(data["audioContent"])
            except (KeyError, ValueError, TypeError) as error:
                raise GoogleTtsError("Gemini の読み上げの応答を読めません") from error
        else:
            content: dict[str, Any] = {"type": "text", "text": text}
            if style:
                content["annotations"] = [{"type": "speech_metadata", "style": style}]
            data = self._transport("POST", GEMINI_INTERACTIONS_URL, {
                "model": model,
                "input": [{"type": "user_input", "content": [content]}],
                "response_format": {"type": "audio", "mime_type": "audio/wav"},
                "generation_config": {"speech_config": [{"voice": voice}]},
            }, GEMINI_TIMEOUT_SECONDS)
            audio = _gemini_audio(data)
        with self._lock:
            self._cache[key] = audio
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        return audio

    def speak(
        self, text: str, settings: SpeechSettings, cancel: Event,
        progress: SpeechProgress | None = None,
    ) -> bool:
        from .tts import GEMINI_TTS_MODELS

        backend = settings.gemini_backend
        models = GEMINI_TTS_MODELS[backend]
        # A model saved for the other backend falls back to this backend's default.
        model = settings.gemini_model if settings.gemini_model in models else models[0]
        voice, style = settings.gemini_voice, settings.gemini_style
        return speak_sentences(
            text, lambda sentence: self.synthesize(sentence, model=model, voice=voice,
                                                   style=style, backend=backend),
            suffix=".mp3" if backend == "cloud" else ".wav", pool=self._pool,
            player=self._player, volume=settings.volume, cancel=cancel, progress=progress,
        )


def _gemini_audio(data: dict[str, Any]) -> bytes:
    """The last audio part of the model output in an interactions response."""
    found: str | None = None
    for step in data.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for part in step.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "audio" and part.get("data"):
                found = str(part["data"])
    if found is None:
        output = data.get("output_audio")
        if isinstance(output, dict) and output.get("data"):
            found = str(output["data"])
    if found is None:
        raise GoogleTtsError("Gemini の読み上げの応答に音声がありません")
    try:
        return base64.b64decode(found)
    except (ValueError, TypeError) as error:
        raise GoogleTtsError("Gemini の読み上げの応答を読めません") from error


def _play_with_afplay(path: Path, volume: float, cancel: Event) -> bool:
    from .tts import _play_with_afplay as play

    return play(path, volume, cancel)
