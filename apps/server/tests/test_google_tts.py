"""Google Cloud Text-to-Speech engine, provider switching and the macOS fallback."""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from commentator.main import MemoryGameRepository, create_app
from commentator.services import google_tts
from commentator.services.google_tts import GoogleTtsEngine, GoogleTtsError, split_sentences
from commentator.services.recognition import RecognitionService
from commentator.services.tts import (
    SpeechService,
    SpeechSettings,
    SpeechSettingsStore,
    Voice,
)
from fastapi.testclient import TestClient


class FakeTransport:
    def __init__(self, *, fail: bool = False, delay: float = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.delay = delay

    def __call__(self, method: str, url: str, body: dict[str, Any] | None,
                 timeout: float) -> dict[str, Any]:
        self.calls.append({"method": method, "url": url, "body": body, "at": time.monotonic()})
        if self.fail:
            raise GoogleTtsError("Google の読み上げに接続できませんでした")
        if self.delay:
            time.sleep(self.delay)
        if method == "GET":
            return {"voices": [
                {"name": "ja-JP-Wavenet-B", "ssmlGender": "FEMALE"},
                {"name": "ja-JP-Neural2-C", "ssmlGender": "MALE"},
                {"name": "en-US-Wavenet-A", "ssmlGender": "MALE"},
            ]}
        assert body is not None
        audio = f"audio:{body['input']['text']}".encode()
        return {"audioContent": base64.b64encode(audio).decode()}


class FakePlayer:
    def __init__(self, *, seconds: float = 0.0) -> None:
        self.played: list[tuple[bytes, float, float]] = []
        self.seconds = seconds

    def __call__(self, path: Path, volume: float, cancel: Event) -> bool:
        self.played.append((path.read_bytes(), volume, time.monotonic()))
        if self.seconds:
            cancel.wait(self.seconds)
        return not cancel.is_set()


def engine(transport: FakeTransport, player: FakePlayer) -> GoogleTtsEngine:
    return GoogleTtsEngine(transport=transport, player=player, key_present=lambda: True)


SETTINGS = SpeechSettings(provider="google", google_voice="ja-JP-Wavenet-B",
                          google_speaking_rate=1.2, volume=0.6)
COMMENT = "あおいさん、出目は4！12マス目から16マス目へ進みます。ハシゴを登って一気に38マス目！"


def test_sentences_are_split_and_tiny_pieces_merged() -> None:
    assert split_sentences(COMMENT) == [
        "あおいさん、出目は4！",
        "12マス目から16マス目へ進みます。",
        "ハシゴを登って一気に38マス目！",
    ]
    assert split_sentences("ゴール！おめでとう！") == ["ゴール！おめでとう！"]
    assert split_sentences("句点なしの文") == ["句点なしの文"]
    assert split_sentences("  ") == []


def test_speaks_sentence_by_sentence_and_prefetches_the_next() -> None:
    transport, player = FakeTransport(delay=0.05), FakePlayer(seconds=0.1)
    assert engine(transport, player).speak(COMMENT, SETTINGS, Event()) is True
    assert [item[0].decode() for item in player.played] == [
        f"audio:{sentence}" for sentence in split_sentences(COMMENT)
    ]
    assert all(item[1] == 0.6 for item in player.played)
    body = transport.calls[0]["body"]
    assert body["voice"] == {"languageCode": "ja-JP", "name": "ja-JP-Wavenet-B"}
    assert body["audioConfig"] == {"audioEncoding": "MP3", "speakingRate": 1.2}
    # The second sentence was requested while the first was playing.
    assert transport.calls[1]["at"] < player.played[1][2]
    assert transport.calls[1]["at"] < player.played[0][2] + 0.1


def test_repeated_sentences_come_from_the_cache() -> None:
    transport, player = FakeTransport(), FakePlayer()
    tts = engine(transport, player)
    tts.speak("同じ文をもう一度読みます。", SETTINGS, Event())
    tts.speak("同じ文をもう一度読みます。", SETTINGS, Event())
    assert len(transport.calls) == 1
    assert len(player.played) == 2


def test_cancel_stops_before_the_next_sentence() -> None:
    transport, player = FakeTransport(), FakePlayer(seconds=0.5)
    cancel = Event()
    tts = engine(transport, player)
    started = time.monotonic()
    from threading import Timer

    Timer(0.1, cancel.set).start()
    assert tts.speak(COMMENT, SETTINGS, cancel) is False
    assert len(player.played) == 1
    assert time.monotonic() - started < 0.5


def test_voices_are_japanese_only() -> None:
    tts = engine(FakeTransport(), FakePlayer())
    assert tts.voices() == [
        Voice("ja-JP-Neural2-C", "ja-JP", "Neural2 男性"),
        Voice("ja-JP-Wavenet-B", "ja-JP", "Wavenet 女性"),
    ]
    assert GoogleTtsEngine(transport=FakeTransport(), key_present=lambda: False).voices() == []
    assert not GoogleTtsEngine(transport=FakeTransport(), key_present=lambda: False).available()


def test_http_errors_become_plain_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(google_tts.KEY_ENV, "secret-key")
    seen: dict[str, Any] = {}

    def forbidden(request: Any, timeout: float, context: Any = None) -> Any:
        seen["verified"] = context is not None
        seen["key"] = request.get_header("X-goog-api-key")
        seen["url"] = request.full_url
        body = json.dumps({"error": {"message": "API not enabled"}}).encode()
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", Message(),
                                     io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    with pytest.raises(GoogleTtsError, match="APIキー") as raised:
        google_tts._http("POST", f"{google_tts.API_ROOT}/text:synthesize", {"x": 1}, 1.0)
    assert seen["key"] == "secret-key"
    assert seen["verified"]  # HTTPS is verified with an explicit CA bundle
    assert "secret-key" not in seen["url"]  # the key travels in a header only
    assert "secret-key" not in str(raised.value)


class FailingEngine:
    name = "google"

    def available(self) -> bool:
        return True

    def voices(self) -> list[Voice]:
        return []

    def speak(self, text: str, settings: SpeechSettings, cancel: Event) -> bool:
        raise GoogleTtsError("Google の読み上げに接続できませんでした")


class RecordingEngine:
    name = "macos"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def available(self) -> bool:
        return True

    def voices(self) -> list[Voice]:
        return [Voice("Kyoko", "ja_JP", "こんにちは")]

    def speak(self, text: str, settings: SpeechSettings, cancel: Event) -> bool:
        self.spoken.append(text)
        return True


def test_google_failure_falls_back_to_the_macos_voice() -> None:
    mac = RecordingEngine()
    store = SpeechSettingsStore()
    store.update({"provider": "google"})
    service = SpeechService(engines={"macos": mac, "google": FailingEngine()}, settings=store)
    service.start()
    try:
        service.preview("テストです。")
        deadline = time.monotonic() + 2
        while not mac.spoken:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert mac.spoken == ["テストです。"]
        time.sleep(0.05)
        assert service.snapshot()["status"] == "idle"
    finally:
        service.stop()


def test_settings_api_switches_provider_and_reports_availability(tmp_path: Path) -> None:
    google = engine(FakeTransport(), FakePlayer())
    service = SpeechService(
        engines={"macos": RecordingEngine(), "google": google},
        settings=SpeechSettingsStore(tmp_path / "speech.json"),
    )
    client = TestClient(create_app(
        repository=MemoryGameRepository(), speech_service=service,
        recognition_service=RecognitionService(None, pipeline=None, camera_provider=None),  # type: ignore[arg-type]
    ))
    body = client.get("/api/settings/speech").json()
    assert body["provider"] == "macos"
    assert body["providers"] == {"macos": True, "google": True, "googleKeyConfigured": True}
    updated = client.put("/api/settings/speech", json={
        "provider": "google", "googleVoice": "ja-JP-Neural2-C", "googleSpeakingRate": 1.3,
    }).json()
    assert updated["provider"] == "google" and updated["engine"] == "google"
    assert updated["googleVoice"] == "ja-JP-Neural2-C"
    saved = json.loads((tmp_path / "speech.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "google"
    for bad in ({"provider": "azure"}, {"googleSpeakingRate": 3}, {"provider": None},
                {"googleVoice": "fr-FR-x"}):
        assert client.put("/api/settings/speech", json=bad).status_code == 422, bad
    voices = client.get("/api/speech/voices", params={"provider": "google"}).json()["voices"]
    assert [voice["name"] for voice in voices] == ["ja-JP-Neural2-C", "ja-JP-Wavenet-B"]
    assert client.get("/api/speech/voices", params={"provider": "macos"}).json()["voices"][0][
        "name"] == "Kyoko"
    assert client.get("/api/status").json()["tts"]["detail"] == "Google（ja-JP-Neural2-C）"
