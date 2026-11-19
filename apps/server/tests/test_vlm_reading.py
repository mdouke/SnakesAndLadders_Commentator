"""Reading the die with the vision model and switching between methods (ADR 0006)."""

from __future__ import annotations

import time
from itertools import count
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from commentator.domain.game import GameState, PlayerState, RollSource
from commentator.infrastructure.persistence.sqlite import game_from_snapshot, game_to_snapshot
from commentator.main import MemoryGameRepository, create_app
from commentator.presets import load_board_preset
from commentator.services.game import GameService
from commentator.services.recognition import RecognitionService
from commentator.services.vision.recognition import (
    ConfirmationSource,
    DiceRecognitionSession,
    DieObservation,
    FrameObservation,
    RecognitionConfig,
    RecognitionState,
    VisionReading,
)
from commentator.services.vision.vlm import (
    OUTPUT_SCHEMA,
    FakeDiceReader,
    VlmError,
    VlmSettings,
    VlmSettingsStore,
    parse_output,
)
from fastapi.testclient import TestClient

from .test_recognition_api import FakeCameraProvider, ObservationPipeline

CONFIG = RecognitionConfig(
    stationary_frames=1, consecutive_value_frames=1, candidate_window_frames=1
)
SURE_FOUR = VisionReading(dice_count=1, value=4, confidence="high", note="4つ", model="m")
UNSURE_FIVE = VisionReading(dice_count=1, value=5, confidence="medium", model="m")


def die(value: int, confidence: float = 1.0) -> FrameObservation:
    return FrameObservation(0, 0.0, detections=(DieObservation(value, confidence, (10, 10)),),
                            roi_diagonal=100)


class Clock:
    def __init__(self) -> None:
        self.ticks = count(1)

    def __call__(self) -> float:
        return float(next(self.ticks))


def session(mode: str) -> tuple[DiceRecognitionSession, Clock]:
    item = DiceRecognitionSession(CONFIG)
    item.reading_mode = mode  # type: ignore[assignment]
    return item, Clock()


def observe(item: DiceRecognitionSession, clock: Clock, frame: FrameObservation) -> Any:
    return item.observe(FrameObservation(
        clock(), frame.motion_score, frame.detections, frame.rejected_count,
        frame.roi_diagonal,
    ))


def throw_and_settle(item: DiceRecognitionSession, clock: Clock,
                     frame: FrameObservation | None = None) -> Any:
    observe(item, clock, FrameObservation(0, 0.2))  # thrown
    snapshot = observe(item, clock, FrameObservation(0, 0.0))  # still
    if frame is not None and snapshot.state is RecognitionState.STATIONARY:
        snapshot = observe(item, clock, frame)
    return snapshot


# --- session --------------------------------------------------------------

def test_vlm_mode_reads_after_the_die_stops_even_without_opencv() -> None:
    item, clock = session("vlm")
    snapshot = throw_and_settle(item, clock)
    assert snapshot.state is RecognitionState.READING
    assert snapshot.reading_id is not None
    # Still frames while waiting do not change anything.
    assert observe(item, clock, FrameObservation(0, 0.0)).state is RecognitionState.READING

    assert item.complete_reading(snapshot.reading_id, SURE_FOUR, monotonic_time=clock())
    confirmed = item.snapshot()
    assert confirmed.state is RecognitionState.CONFIRMED
    assert confirmed.confirmation is not None
    assert confirmed.confirmation.value == 4
    assert confirmed.confirmation.source is ConfirmationSource.AUTOMATIC
    recognition = confirmed.confirmation.recognition
    assert recognition is not None
    assert recognition["recognizer"] == "vlm"
    assert recognition["vlm"]["value"] == 4
    assert recognition["opencvCandidate"] is None


def test_vlm_confirmation_needs_motion_before_the_next_throw() -> None:
    item, clock = session("vlm")
    snapshot = throw_and_settle(item, clock)
    assert snapshot.reading_id is not None
    item.complete_reading(snapshot.reading_id, SURE_FOUR, monotonic_time=clock())
    confirmation = item.snapshot().confirmation
    assert confirmation is not None
    item.acknowledge_committed(confirmation.id)
    # OpenCV loses the die, then sees it again in place: not a new throw.
    for _ in range(4):
        observe(item, clock, FrameObservation(0, 0.0))
    assert item.state is RecognitionState.WAITING
    assert observe(item, clock, die(4)).state is RecognitionState.WAITING
    assert observe(item, clock, FrameObservation(0, 0.2)).state is RecognitionState.MOVING


@pytest.mark.parametrize(("reading", "error", "state", "candidate", "code"), [
    (UNSURE_FIVE, None, RecognitionState.NEEDS_CONFIRMATION, 5, None),
    (VisionReading(2, 3, "high"), None, RecognitionState.NEEDS_CONFIRMATION, 3, None),
    (VisionReading(1, None, "low"), None, RecognitionState.RECOGNITION_ERROR, None,
     "vlm_unreadable"),
    (None, "timeout", RecognitionState.RECOGNITION_ERROR, None, "vlm_timeout"),
    (VisionReading(0, None, "high"), None, RecognitionState.WAITING, None, None),
])
def test_vlm_mode_decisions(reading: VisionReading | None, error: str | None,
                            state: RecognitionState, candidate: int | None,
                            code: str | None) -> None:
    item, clock = session("vlm")
    snapshot = throw_and_settle(item, clock)
    assert snapshot.reading_id is not None
    item.complete_reading(snapshot.reading_id, reading, monotonic_time=clock(),
                          error_code=error)
    result = item.snapshot()
    assert result.state is state
    assert result.candidate_value == candidate
    assert result.error_code == code


def test_reviewed_vlm_candidate_keeps_the_reading() -> None:
    item, clock = session("vlm")
    snapshot = throw_and_settle(item, clock)
    assert snapshot.reading_id is not None
    item.complete_reading(snapshot.reading_id, UNSURE_FIVE, monotonic_time=clock())
    confirmation = item.confirm_candidate(value=6, monotonic_time=clock())
    assert confirmation.source is ConfirmationSource.CORRECTED
    assert confirmation.original_candidate == 5
    assert confirmation.recognition is not None
    assert confirmation.recognition["vlm"]["value"] == 5


def test_motion_while_reading_makes_the_answer_stale() -> None:
    item, clock = session("vlm")
    snapshot = throw_and_settle(item, clock)
    old = snapshot.reading_id
    assert old is not None
    assert observe(item, clock, FrameObservation(0, 0.3)).state is RecognitionState.MOVING
    assert not item.complete_reading(old, SURE_FOUR, monotonic_time=clock())
    again = observe(item, clock, FrameObservation(0, 0.0))
    assert again.state is RecognitionState.READING
    assert again.reading_id not in {None, old}


@pytest.mark.parametrize(("opencv_confidence", "reading", "state", "candidate"), [
    (1.0, SURE_FOUR, RecognitionState.CONFIRMED, 4),       # agree
    (0.7, SURE_FOUR, RecognitionState.CONFIRMED, 4),       # agree, model sure
    (1.0, VisionReading(1, 4, "low"), RecognitionState.CONFIRMED, 4),  # agree, OpenCV sure
    (0.7, VisionReading(1, 4, "low"), RecognitionState.NEEDS_CONFIRMATION, 4),
    (1.0, VisionReading(1, 6, "high"), RecognitionState.NEEDS_CONFIRMATION, 6),  # disagree
    (1.0, VisionReading(1, 6, "medium"), RecognitionState.NEEDS_CONFIRMATION, 4),
    (1.0, VisionReading(1, None, "low"), RecognitionState.NEEDS_CONFIRMATION, 4),
    (1.0, None, RecognitionState.CONFIRMED, 4),            # model down: OpenCV decides
    (0.7, None, RecognitionState.NEEDS_CONFIRMATION, 4),
])
def test_hybrid_mode_decisions(opencv_confidence: float, reading: VisionReading | None,
                               state: RecognitionState, candidate: int) -> None:
    item, clock = session("hybrid")
    snapshot = throw_and_settle(item, clock, die(4, opencv_confidence))
    assert snapshot.state is RecognitionState.READING
    assert snapshot.reading_id is not None
    item.complete_reading(snapshot.reading_id, reading, monotonic_time=clock(),
                          error_code=None if reading else "connection")
    result = item.snapshot()
    assert result.state is state
    assert result.candidate_value == candidate
    assert result.recognition is not None
    assert result.recognition["recognizer"] == "hybrid"
    assert result.recognition["opencvCandidate"] == 4
    if state is RecognitionState.CONFIRMED:
        assert result.confirmation is not None
        assert result.confirmation.original_candidate == 4


def test_opencv_mode_is_unchanged_and_labelled() -> None:
    item, clock = session("opencv")
    snapshot = throw_and_settle(item, clock, die(3))
    assert snapshot.state is RecognitionState.CONFIRMED
    assert snapshot.confirmation is not None
    assert snapshot.confirmation.recognition == {
        "recognizer": "opencv", "readingId": None, "opencvCandidate": 3,
        "vlm": None, "vlmError": None,
    }


# --- reader ---------------------------------------------------------------

def test_output_parsing_and_schema() -> None:
    reading = parse_output('{"diceCount":1,"value":0,"confidence":"low","note":"傾き"}',
                           model="m", duration_ms=5)
    assert reading.value is None and reading.dice_count == 1
    for bad in ("not json", '{"diceCount":1}', '{"diceCount":9,"value":1,"confidence":"high"}',
                '{"diceCount":1,"value":1,"confidence":"sure","note":""}'):
        with pytest.raises(VlmError):
            parse_output(bad, model="m", duration_ms=0)
    assert OUTPUT_SCHEMA["additionalProperties"] is False


def test_settings_store_validates(tmp_path: Path) -> None:
    store = VlmSettingsStore(tmp_path / "vlm.json")
    assert store.get() == VlmSettings()
    store.update({"readingMode": "hybrid", "model": "claude-haiku-5-5"})
    assert VlmSettingsStore(tmp_path / "vlm.json").get().reading_mode == "hybrid"
    with pytest.raises(ValueError):
        store.update({"readingMode": "magic"})
    with pytest.raises(ValueError):
        store.update({"timeoutSeconds": 100})


# --- service ----------------------------------------------------------------

def prepared(
    reader: FakeDiceReader, mode: str = "vlm", *, max_retries: int = 1
) -> tuple[RecognitionService, MemoryGameRepository, GameState]:
    repository = MemoryGameRepository()
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"), PlayerState("b", "B", "blue"),
    ])
    state.start()
    repository.save_and_set_current(state)
    store = VlmSettingsStore()
    store.update({"readingMode": mode, "maxRetries": max_retries})
    service = RecognitionService(
        GameService(repository),
        pipeline=ObservationPipeline(DiceRecognitionSession(CONFIG)),
        camera_provider=FakeCameraProvider(),
        clock=Clock(),
        dice_reader=reader,
        vlm_settings=store,
        image_encoder=lambda image: b"jpeg",
        retry_delay_seconds=0.0,
    )
    return service, repository, state


def wait_for(condition: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met")
        time.sleep(0.005)


def test_service_reads_and_registers_the_turn() -> None:
    reader = FakeDiceReader(SURE_FOUR)
    service, repository, state = prepared(reader)
    service.process_frame(FrameObservation(0, 0.2))
    snapshot = service.process_frame(FrameObservation(0, 0.0))
    assert snapshot["state"] in {"reading", "removal_wait"}
    assert snapshot["readingMode"] == "vlm"
    wait_for(lambda: len(repository.get(state.id).turns) == 1)  # type: ignore[union-attr]
    turn = repository.get(state.id).turns[0]  # type: ignore[union-attr]
    assert turn.roll == 4
    assert turn.source is RollSource.AUTOMATIC
    assert turn.recognition is not None and turn.recognition["recognizer"] == "vlm"
    assert reader.calls[0]["model"] == VlmSettings().model
    wait_for(lambda: service.recent_readings()[0]["status"] == "used")
    # The reading survives a save/restore of the game.
    restored = game_from_snapshot(game_to_snapshot(repository.get(state.id)))  # type: ignore[arg-type]
    assert restored.turns[0].recognition == turn.recognition
    assert restored.to_dict()["turns"][0]["recognition"]["vlm"]["value"] == 4


def test_answer_after_the_die_moved_is_discarded() -> None:
    gate = Event()

    class SlowReader(FakeDiceReader):
        def read(self, image_jpeg: bytes, *, model: str, timeout: float) -> VisionReading:
            gate.wait(2)
            return super().read(image_jpeg, model=model, timeout=timeout)

    service, repository, state = prepared(SlowReader(SURE_FOUR))
    service.process_frame(FrameObservation(0, 0.2))
    service.process_frame(FrameObservation(0, 0.0))
    assert service.snapshot()["state"] == "reading"
    service.process_frame(FrameObservation(0, 0.3))  # picked up again
    gate.set()
    wait_for(lambda: bool(service.recent_readings()))
    assert service.recent_readings()[0]["status"] == "discarded"
    assert repository.get(state.id).turns == []  # type: ignore[union-attr]


def test_retryable_errors_are_retried_and_failures_fall_back() -> None:
    reader = FakeDiceReader(VlmError("t", code="timeout", retryable=True), SURE_FOUR)
    service, repository, state = prepared(reader)
    service.process_frame(FrameObservation(0, 0.2))
    service.process_frame(FrameObservation(0, 0.0))
    wait_for(lambda: len(repository.get(state.id).turns) == 1)  # type: ignore[union-attr]
    assert len(reader.calls) == 2

    failing = FakeDiceReader(VlmError("a", code="auth", retryable=False))
    service, repository, state = prepared(failing, "hybrid")
    drive = [FrameObservation(0, 0.2), FrameObservation(0, 0.0), die(4)]
    for frame in drive:
        service.process_frame(frame)
    # The model is down, so OpenCV decides alone (as in the opencv mode).
    wait_for(lambda: len(repository.get(state.id).turns) == 1)  # type: ignore[union-attr]
    turn = repository.get(state.id).turns[0]  # type: ignore[union-attr]
    assert turn.roll == 4
    assert turn.recognition is not None and turn.recognition["vlmError"] == "auth"
    assert len(failing.calls) == 1


def test_mode_change_waits_for_the_current_throw() -> None:
    gate = Event()

    class SlowReader(FakeDiceReader):
        def read(self, image_jpeg: bytes, *, model: str, timeout: float) -> VisionReading:
            gate.wait(2)
            return super().read(image_jpeg, model=model, timeout=timeout)

    service, repository, state = prepared(SlowReader(SURE_FOUR))
    service.process_frame(FrameObservation(0, 0.2))
    service.process_frame(FrameObservation(0, 0.0))
    payload = service.update_vlm_settings({"readingMode": "opencv"})
    assert payload["pendingReadingMode"] is True
    assert payload["activeReadingMode"] == "vlm"
    gate.set()
    wait_for(lambda: len(repository.get(state.id).turns) == 1)  # type: ignore[union-attr]
    service.process_frame(FrameObservation(0, 0.0))  # removal_wait -> applies
    assert service.vlm_settings_payload()["activeReadingMode"] == "opencv"


def test_api_settings_and_test_reading() -> None:
    service, _repository, _state = prepared(FakeDiceReader(SURE_FOUR), "opencv")
    client = TestClient(create_app(recognition_service=service))
    body = client.get("/api/settings/vlm").json()
    assert body["readingMode"] == "opencv"
    assert body["readerConfigured"] is True
    assert "apiKey" not in body
    updated = client.put("/api/settings/vlm", json={"readingMode": "hybrid",
                                                   "model": "claude-haiku-5-5"})
    assert updated.status_code == 200
    assert updated.json()["activeReadingMode"] == "hybrid"
    for bad in ({"readingMode": "magic"}, {"timeoutSeconds": 1}, {"apiKey": "x"},
                {"model": None}):
        assert client.put("/api/settings/vlm", json=bad).status_code == 422, bad
    assert client.post("/api/recognition/vlm/test").status_code == 409
    assert client.get("/api/recognition/vlm/readings").json() == {"readings": []}
    assert client.get("/api/recognition").json()["readingMode"] == "hybrid"
