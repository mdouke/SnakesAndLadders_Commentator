from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock
from typing import Any

import pytest
from commentator.main import MemoryGameRepository, create_app
from commentator.services.commentary import CommentaryService
from commentator.services.game import GameService
from commentator.services.llm.providers import FakeCommentaryProvider
from commentator.services.recognition import RecognitionService
from commentator.services.tts import (
    SpeechService,
    SpeechSettings,
    SpeechSettingsStore,
    Voice,
    apply_readings,
    parse_voices,
)
from fastapi.testclient import TestClient

SAY_OUTPUT = """\
Albert              en_US    # Hello! My name is Albert.
Eddy (日本語（日本）)      ja_JP    # こんにちは! 私の名前はEddyです。
Kyoko               ja_JP    # こんにちは! 私の名前はKyokoです。
"""


class FakeEngine:
    """Records speech; ``hold`` keeps each utterance going until released or cancelled."""

    name = "fake"

    def __init__(self, *, hold: bool = False, fail: bool = False) -> None:
        self.hold = hold
        self.fail = fail
        self.spoken: list[tuple[str, SpeechSettings]] = []
        self.cancelled: list[str] = []
        self.started = Event()
        self.release = Event()
        self._lock = Lock()

    def available(self) -> bool:
        return True

    def voices(self) -> list[Voice]:
        return parse_voices(SAY_OUTPUT)

    def speak(self, text: str, settings: SpeechSettings, cancel: Event,
              progress: Any = None) -> bool:
        with self._lock:
            self.spoken.append((text, settings))
        if self.fail:
            self.started.set()
            raise RuntimeError("audio device missing")
        if progress is not None:
            progress.audio_started()  # the sound starts right away
        self.started.set()
        if not self.hold:
            return True
        while not self.release.is_set():
            if cancel.wait(0.01):
                self.cancelled.append(text)
                return False
        return True


def wait_until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


def ready(request_id: str, text: str) -> dict[str, Any]:
    return {"status": "ready", "requestId": request_id, "turnId": "t", "text": text}


def test_parse_voices_keeps_names_with_spaces() -> None:
    voices = parse_voices(SAY_OUTPUT)
    assert [voice.name for voice in voices] == ["Albert", "Eddy (日本語（日本）)", "Kyoko"]
    assert voices[1].locale == "ja_JP"
    assert voices[2].sample == "こんにちは! 私の名前はKyokoです。"


def test_readings_replace_longer_names_first() -> None:
    text = "ハルカさんとハルさんの勝負"
    readings = {"ハル": "はる", "ハルカ": "はるか"}
    assert apply_readings(text, readings) == "はるかさんとはるさんの勝負"


def test_speaks_the_current_comment_with_readings() -> None:
    engine = FakeEngine()
    service = SpeechService(
        engine=engine, is_current=lambda request_id: request_id == "r1",
        readings=lambda: {"葵": "あおい"},
    )
    states: list[str] = []
    service.subscribe(lambda state: states.append(state["status"]))
    service.start()
    try:
        service.commentary_changed(ready("r1", "葵さん、出目は3！"))
        wait_until(lambda: states[-2:] == ["speaking", "idle"])
    finally:
        service.stop()
    assert engine.spoken[0][0] == "あおいさん、出目は3！"


def test_a_comment_that_is_no_longer_current_is_not_spoken() -> None:
    engine = FakeEngine()
    service = SpeechService(engine=engine, is_current=lambda _request_id: False)
    service.start()
    try:
        service.commentary_changed(ready("old", "古い実況"))
        time.sleep(0.1)
    finally:
        service.stop()
    assert engine.spoken == []


def test_a_new_comment_or_undo_stops_the_speech_in_progress() -> None:
    engine = FakeEngine(hold=True)
    current = {"id": "r1"}
    service = SpeechService(engine=engine, is_current=lambda rid: rid == current["id"])
    service.start()
    try:
        service.commentary_changed(ready("r1", "一つ目"))
        wait_until(engine.started.is_set)
        current["id"] = "r2"
        service.commentary_changed({"status": "generating", "requestId": "r2", "text": None})
        wait_until(lambda: engine.cancelled == ["一つ目"])
        engine.release.set()
        service.commentary_changed(ready("r2", "二つ目"))
        wait_until(lambda: [text for text, _ in engine.spoken] == ["一つ目", "二つ目"])

        current["id"] = None  # type: ignore[assignment]
        service.commentary_changed({"status": "idle", "requestId": None, "text": None})
    finally:
        service.stop()


def test_disabled_speech_stays_silent_and_turning_off_stops_it() -> None:
    engine = FakeEngine(hold=True)
    settings = SpeechSettingsStore()
    service = SpeechService(engine=engine, settings=settings, is_current=lambda _r: True)
    service.start()
    try:
        service.commentary_changed(ready("r1", "読み上げ中"))
        wait_until(engine.started.is_set)
        settings.update({"enabled": False})
        service.settings_changed()
        wait_until(lambda: engine.cancelled == ["読み上げ中"])
        service.commentary_changed(ready("r2", "オフなので読まない"))
        time.sleep(0.1)
    finally:
        service.stop()
    assert [text for text, _ in engine.spoken] == ["読み上げ中"]


def test_preview_uses_the_given_settings_and_errors_are_reported() -> None:
    engine = FakeEngine(fail=True)
    service = SpeechService(engine=engine)
    states: list[dict[str, Any]] = []
    service.subscribe(states.append)
    service.start()
    try:
        service.preview(None, SpeechSettings(voice="Kyoko", rate=200, volume=0.5))
        wait_until(lambda: bool(states) and states[-1]["status"] == "error")
    finally:
        service.stop()
    text, settings = engine.spoken[0]
    assert "ヘビとハシゴ" in text
    assert settings.voice == "Kyoko" and settings.volume == 0.5
    assert states[-1]["error"] == "audio device missing"


def test_settings_are_validated_and_saved(tmp_path: Path) -> None:
    path = tmp_path / "speech.json"
    store = SpeechSettingsStore(path)
    store.update({"voice": "Kyoko", "rate": 220, "volume": 0.7})
    assert SpeechSettingsStore(path).get() == SpeechSettings(voice="Kyoko", rate=220, volume=0.7)
    with pytest.raises(ValueError):
        store.update({"rate": 10})


def test_end_to_end_roll_is_spoken_and_undo_stops_it() -> None:
    repository = MemoryGameRepository()
    games = GameService(repository)
    commentary = CommentaryService(games, providers={"fake": FakeCommentaryProvider()})
    engine = FakeEngine(hold=True)
    speech = SpeechService(engine=engine)
    app = create_app(
        repository=repository,
        recognition_service=RecognitionService(games, pipeline=None, camera_provider=None),
        commentary_service=commentary,
        speech_service=speech,
    )
    players = {"players": [
        {"name": "葵", "color": "red", "spokenName": "あおい"},
        {"name": "B", "color": "blue"},
    ]}
    with TestClient(app) as http:
        game = http.post("/api/games", json=players).json()
        game = http.post(f"/api/games/{game['id']}/start",
                         json={"expectedStateVersion": 0}).json()
        game = http.post(f"/api/games/{game['id']}/rolls",
                         json={"value": 3, "expectedStateVersion": game["stateVersion"]}).json()
        wait_until(engine.started.is_set)
        assert engine.spoken[0][0].startswith("あおいさん、出目は3")
        assert http.get("/api/speech").json()["status"] == "speaking"

        http.post(f"/api/games/{game['id']}/undo",
                  json={"expectedStateVersion": game["stateVersion"]})
        wait_until(lambda: len(engine.cancelled) == 1)
        wait_until(lambda: http.get("/api/speech").json()["status"] == "idle")

        voices = http.get("/api/speech/voices").json()["voices"]
        assert voices[1]["locale"] == "ja_JP"
        updated = http.put("/api/settings/speech", json={"voice": "Kyoko", "rate": None})
        assert updated.status_code == 200 and updated.json()["voice"] == "Kyoko"
        assert http.put("/api/settings/speech", json={"volume": 2}).status_code == 422
        assert http.post("/api/speech/preview", json={"voice": "Kyoko"}).status_code == 200


def test_macos_voice_renders_sentence_by_sentence_and_starts_after_the_first() -> None:
    from commentator.services.tts import MacSayEngine, SpeechProgress

    rendered: list[tuple[str, float]] = []
    played: list[tuple[bytes, float, float]] = []

    def render(text: str, settings: SpeechSettings, cancel: Event) -> bytes:
        rendered.append((text, time.monotonic()))
        time.sleep(0.05)
        return f"aiff:{text}:{settings.rate}".encode()

    def player(path: Path, volume: float, cancel: Event) -> bool:
        played.append((path.read_bytes(), volume, time.monotonic()))
        cancel.wait(0.1)
        return not cancel.is_set()

    comment = "あおいさん、出目は4！12マス目から16マス目へ進みます。ハシゴを登って38マス目！"
    progress = SpeechProgress(comment)
    engine = MacSayEngine(render=render, player=player)
    assert engine.speak(comment, SpeechSettings(rate=230, volume=0.5), Event(), progress)
    assert [text for text, _ in rendered] == [
        "あおいさん、出目は4！", "12マス目から16マス目へ進みます。", "ハシゴを登って38マス目！",
    ]
    assert [audio for audio, _, _ in played] == [
        f"aiff:{text}:230".encode() for text, _ in rendered
    ]
    assert all(volume == 0.5 for _, volume, _ in played)
    # The second sentence was rendered while the first one was playing.
    assert rendered[1][1] < played[0][2] + 0.1
    assert progress.remaining == ""


class SlowStartEngine:
    """Takes a moment to make the voice before the first sound."""

    name = "slow"

    def __init__(self) -> None:
        self.make_voice = Event()

    def available(self) -> bool:
        return True

    def voices(self) -> list[Voice]:
        return []

    def speak(self, text: str, settings: SpeechSettings, cancel: Event,
              progress: Any = None) -> bool:
        self.make_voice.wait(2)
        if progress is not None:
            progress.audio_started()
        return True


def test_comment_is_revealed_when_its_sound_starts() -> None:
    engine = SlowStartEngine()
    service = SpeechService(engine=engine, is_current=lambda _r: True)
    states: list[dict[str, Any]] = []
    service.subscribe(states.append)
    service.start()
    try:
        service.commentary_changed(ready("r1", "葵さん、出目は3！"))
        wait_until(lambda: bool(states) and states[-1]["status"] == "preparing")
        # The voice is still being made: the player screen keeps waiting.
        assert states[-1]["revealedRequestId"] is None
        engine.make_voice.set()
        wait_until(lambda: any(s["status"] == "speaking" for s in states))
        speaking = next(s for s in states if s["status"] == "speaking")
        assert speaking["revealedRequestId"] == "r1"
        wait_until(lambda: states[-1]["status"] == "idle")
        assert states[-1]["revealedRequestId"] == "r1"
    finally:
        service.stop()


def test_comment_is_revealed_when_speech_fails_or_is_off() -> None:
    service = SpeechService(engine=FakeEngine(fail=True), is_current=lambda _r: True)
    service.start()
    try:
        service.commentary_changed(ready("r1", "失敗する実況"))
        wait_until(lambda: service.snapshot()["status"] == "error")
        assert service.snapshot()["revealedRequestId"] == "r1"

        service.settings.update({"enabled": False})
        service.commentary_changed(ready("r2", "読み上げオフ"))
        assert service.snapshot()["revealedRequestId"] == "r2"
    finally:
        service.stop()
