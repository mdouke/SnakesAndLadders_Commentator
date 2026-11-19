"""Reading commentary aloud with Google Cloud Text-to-Speech.

The REST API is called with an API key (``GOOGLE_TTS_API_KEY``, usually from
``var/secrets.env``); the key is sent in a header and never logged or shown.

To start speaking quickly, the text is split into sentences: the first
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
    from .tts import SpeechSettings, Voice

API_ROOT = "https://texttospeech.googleapis.com/v1"
KEY_ENV = "GOOGLE_TTS_API_KEY"
REQUEST_TIMEOUT_SECONDS = 10.0
_CACHE_SIZE = 64
_SENTENCE = re.compile(r"[^。！？!?]+[。！？!?]*")
_MIN_SENTENCE = 8  # merge tiny pieces ("4！") into the next sentence


class GoogleTtsError(RuntimeError):
    pass


def split_sentences(text: str) -> list[str]:
    """Split after 。！？ and merge pieces too short to be worth a request."""
    pieces = [piece.strip() for piece in _SENTENCE.findall(text) if piece.strip()]
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < _MIN_SENTENCE:
            merged[-1] += piece
        else:
            merged.append(piece)
    if len(merged) > 1 and len(merged[-1]) < _MIN_SENTENCE:
        merged[-2] += merged.pop()
    return merged or ([text.strip()] if text.strip() else [])


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


def _http(method: str, url: str, body: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    key = os.environ.get(KEY_ENV, "")
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"X-Goog-Api-Key": key, "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = json.loads(error.read().decode("utf-8"))["error"]["message"]
        except Exception:
            pass
        if error.code in {401, 403}:
            message = ("Google の読み上げを使えません（APIキー、または Cloud Text-to-Speech API の"
                       "有効化を確認してください）")
        elif error.code == 429:
            message = "Google の読み上げの利用上限に達しました"
        else:
            message = f"Google の読み上げがエラーを返しました（HTTP {error.code}）"
        raise GoogleTtsError(f"{message}: {detail[:200]}" if detail else message) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise GoogleTtsError(
                "Google の読み上げに接続できませんでした（HTTPS の証明書を確認できません）"
            ) from error
        raise GoogleTtsError(
            f"Google の読み上げに接続できませんでした（{type(reason).__name__}）"
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

    def speak(self, text: str, settings: SpeechSettings, cancel: Event) -> bool:
        sentences = split_sentences(text)
        if not sentences:
            return not cancel.is_set()
        voice = settings.google_voice
        rate = settings.google_speaking_rate

        def fetch(sentence: str) -> bytes:
            return self.synthesize(sentence, voice, rate)

        upcoming: Future[bytes] = self._pool.submit(fetch, sentences[0])
        with tempfile.TemporaryDirectory(prefix="commentator-google-tts-") as directory:
            for index in range(len(sentences)):
                audio = upcoming.result()  # raises GoogleTtsError on failure
                if cancel.is_set():
                    return False
                if index + 1 < len(sentences):
                    # Synthesize the next sentence while this one plays.
                    upcoming = self._pool.submit(fetch, sentences[index + 1])
                path = Path(directory) / f"part-{index}.mp3"
                path.write_bytes(audio)
                if not self._player(path, settings.volume, cancel):
                    return False
        return not cancel.is_set()


def _play_with_afplay(path: Path, volume: float, cancel: Event) -> bool:
    from .tts import _run_cancellable

    return _run_cancellable(["afplay", "-v", f"{volume:.2f}", str(path)], cancel)
