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
from commentator.services.google_tts import (
    GeminiTtsEngine,
    GoogleTtsEngine,
    GoogleTtsError,
    split_sentences,
)
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
    assert body["providers"] == {"macos": True, "google": True, "googleKeyConfigured": True,
                                 "gemini": False, "geminiKeyConfigured": False,
                                 "geminiCloudConfigured": False}
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



class FakeGemini:
    def __init__(self, *, reply: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reply = reply

    def __call__(self, method: str, url: str, body: dict[str, Any] | None,
                 timeout: float) -> dict[str, Any]:
        assert body is not None
        self.calls.append({"url": url, "body": body, "timeout": timeout})
        if self.reply is not None:
            return self.reply
        text = body["input"][0]["content"][0]["text"]
        return {"steps": [
            {"type": "user_input"},
            {"type": "model_output", "content": [
                {"type": "text", "text": "ignored"},
                {"type": "audio", "data": base64.b64encode(f"wav:{text}".encode()).decode()},
            ]},
        ]}


GEMINI = SpeechSettings(provider="gemini", gemini_backend="api",
                        gemini_model="gemini-3.8-flash-tts",
                        gemini_voice="Charon", gemini_style="悔しそうに、でも励ますように",
                        volume=0.8)


def gemini_engine(transport: FakeGemini, player: FakePlayer) -> GeminiTtsEngine:
    return GeminiTtsEngine(transport=transport, player=player, key_present=lambda: True,
                           cloud_present=lambda: False)


def test_gemini_sends_the_style_and_plays_wav() -> None:
    transport, player = FakeGemini(), FakePlayer()
    assert gemini_engine(transport, player).speak(COMMENT, GEMINI, Event())
    call = transport.calls[0]
    assert call["url"].endswith("/v1beta/interactions")
    assert call["body"] == {
        "model": "gemini-3.8-flash-tts",
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": "あおいさん、出目は4！",
            "annotations": [{"type": "speech_metadata", "style": "悔しそうに、でも励ますように"}],
        }]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": "Charon"}]},
    }
    assert [item[0].decode() for item in player.played] == [
        f"wav:{sentence}" for sentence in split_sentences(COMMENT)
    ]
    assert player.played[0][1] == 0.8


def test_gemini_style_is_part_of_the_cache_key_and_empty_style_is_omitted() -> None:
    from dataclasses import replace

    transport, player = FakeGemini(), FakePlayer()
    tts = gemini_engine(transport, player)
    tts.speak("同じ文です。", GEMINI, Event())
    tts.speak("同じ文です。", GEMINI, Event())
    assert len(transport.calls) == 1
    tts.speak("同じ文です。", replace(GEMINI, gemini_style=""), Event())
    assert len(transport.calls) == 2
    assert "annotations" not in transport.calls[1]["body"]["input"][0]["content"][0]


def test_gemini_reply_without_audio_is_an_error() -> None:
    tts = gemini_engine(FakeGemini(reply={"steps": [{"type": "model_output", "content": []}]}),
                        FakePlayer())
    with pytest.raises(GoogleTtsError, match="音声がありません"):
        tts.speak("テストです。", GEMINI, Event())


def test_gemini_key_errors_name_the_right_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid(request: Any, timeout: float, context: Any = None) -> Any:
        body = json.dumps({"error": {"message": "API key not valid."}}).encode()
        raise urllib.error.HTTPError(request.full_url, 400, "Bad", Message(), io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", invalid)
    with pytest.raises(GoogleTtsError, match="GEMINI_API_KEY"):
        google_tts._http("POST", google_tts.GEMINI_INTERACTIONS_URL, {}, 1.0,
                         key_env="GEMINI_API_KEY", service="Gemini の読み上げ")


def test_gemini_settings_through_the_api(tmp_path: Path) -> None:
    service = SpeechService(
        engines={"macos": RecordingEngine(), "gemini": gemini_engine(FakeGemini(), FakePlayer())},
        settings=SpeechSettingsStore(tmp_path / "speech.json"),
    )
    client = TestClient(create_app(
        repository=MemoryGameRepository(), speech_service=service,
        recognition_service=RecognitionService(None, pipeline=None, camera_provider=None),  # type: ignore[arg-type]
    ))
    body = client.get("/api/settings/speech").json()
    assert body["geminiModels"]["api"] == ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
    assert body["geminiModels"]["cloud"][0] == "gemini-2.5-flash-lite-preview-tts"
    assert body["geminiBackend"] == "cloud"
    assert {"name": "Kore", "gender": "女性"} in body["geminiVoices"]
    assert body["providers"]["gemini"] is True and body["providers"]["geminiKeyConfigured"]
    updated = client.put("/api/settings/speech", json={
        "provider": "gemini", "geminiVoice": "Puck", "geminiStyle": "落ち着いた解説者のように",
    }).json()
    assert updated["engine"] == "gemini" and updated["geminiVoice"] == "Puck"
    assert client.get("/api/status").json()["tts"]["detail"] == "Gemini（Puck）"
    for bad in ({"geminiVoice": "Nobody"}, {"geminiModel": "wavenet"}, {"geminiStyle": None},
                {"geminiStyle": "あ" * 501}):
        assert client.put("/api/settings/speech", json=bad).status_code == 422, bad
    restored = SpeechSettings.from_dict(GEMINI.to_dict())
    assert restored == GEMINI



def test_gemini_through_cloud_uses_the_service_account_token() -> None:
    calls: list[dict[str, Any]] = []

    def cloud(method: str, url: str, body: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        assert body is not None
        calls.append({"url": url, "body": body})
        return {"audioContent": base64.b64encode(b"mp3").decode()}

    player = FakePlayer()
    tts = GeminiTtsEngine(cloud_transport=cloud, player=player, key_present=lambda: False,
                          cloud_present=lambda: True)
    from dataclasses import replace

    # A model saved for the API falls back to the cloud default.
    settings = replace(GEMINI, gemini_backend="cloud")
    assert tts.speak("ハシゴを登りました。", settings, Event())
    assert calls[0]["url"].endswith("/v1/text:synthesize")
    assert calls[0]["body"] == {
        "input": {"text": "ハシゴを登りました。", "prompt": "悔しそうに、でも励ますように"},
        "voice": {"languageCode": "ja-JP", "name": "Charon",
                  "model_name": "gemini-2.5-flash-lite-preview-tts"},
        "audioConfig": {"audioEncoding": "MP3"},
    }
    assert player.played[0][0] == b"mp3"
    assert tts.available()


def test_bearer_token_replaces_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def ok(request: Any, timeout: float, context: Any = None) -> Any:
        seen["auth"] = request.get_header("Authorization")
        seen["key"] = request.get_header("X-goog-api-key")
        return io.BytesIO(b'{"audioContent": ""}')

    monkeypatch.setattr(urllib.request, "urlopen", ok)
    google_tts._http("POST", f"{google_tts.API_ROOT}/text:synthesize", {}, 1.0,
                     bearer=lambda: "token-123")
    assert seen == {"auth": "Bearer token-123", "key": None}


def test_errors_in_list_form_and_retry_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(request: Any, timeout: float, context: Any = None) -> Any:
        body = json.dumps([{"error": {"code": 403, "message": "Permission denied on the service.",
                                      "details": [{"reason": "ACCESS_DENIED", "metadata": {
                                          "service": "generativelanguage.googleapis.com"}}]}}])
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", Message(),
                                     io.BytesIO(body.encode()))

    monkeypatch.setattr(urllib.request, "urlopen", denied)
    with pytest.raises(GoogleTtsError, match="AI Studio"):
        google_tts._http("POST", google_tts.GEMINI_INTERACTIONS_URL, {}, 1.0,
                         key_env="GEMINI_API_KEY", service="Gemini の読み上げ")

    def limited(request: Any, timeout: float, context: Any = None) -> Any:
        body = json.dumps({"error": {"message": "Rate limit exceeded. Please retry in 1h2m3s."}})
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many", Message(),
                                     io.BytesIO(body.encode()))

    monkeypatch.setattr(urllib.request, "urlopen", limited)
    with pytest.raises(GoogleTtsError) as raised:
        google_tts._http("POST", google_tts.GEMINI_INTERACTIONS_URL, {}, 1.0)
    assert raised.value.retry_after == 3723


class LimitedEngine(FailingEngine):
    name = "gemini"

    def __init__(self) -> None:
        self.calls = 0

    def speak(self, text: str, settings: SpeechSettings, cancel: Event) -> bool:
        self.calls += 1
        raise GoogleTtsError("上限", retry_after=60)


def test_fallback_goes_gemini_then_google_then_macos_and_skips_cooling_providers() -> None:
    gemini, google, mac = LimitedEngine(), RecordingEngine(), RecordingEngine()
    store = SpeechSettingsStore()
    store.update({"provider": "gemini"})
    service = SpeechService(engines={"gemini": gemini, "google": google, "macos": mac},
                            settings=store)
    service.start()
    try:
        for text in ("一つ目です。", "二つ目です。"):
            service.preview(text)
            deadline = time.monotonic() + 2
            while text not in google.spoken:
                assert time.monotonic() < deadline
                time.sleep(0.01)
        assert gemini.calls == 1  # the second comment skipped Gemini while it cools down
        assert mac.spoken == []
    finally:
        service.stop()
