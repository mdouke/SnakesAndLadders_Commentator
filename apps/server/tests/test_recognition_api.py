from __future__ import annotations

import asyncio
import sys
from itertools import count
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from commentator.api.errors import ApiError
from commentator.domain.game import GameState, GameStatus, PlayerState, RollSource
from commentator.infrastructure.persistence import SQLiteGameRepository
from commentator.infrastructure.persistence.sqlite import game_from_snapshot, game_to_snapshot
from commentator.main import MemoryGameRepository, create_app
from commentator.presets import load_board_preset
from commentator.services.game import GameService
from commentator.services.recognition import RecognitionService
from commentator.services.vision.recognition import (
    DiceRecognitionSession,
    DieObservation,
    FrameObservation,
    RecognitionConfig,
)
from httpx import ASGITransport, AsyncClient


class ObservationPipeline:
    def __init__(self, session: DiceRecognitionSession) -> None:
        self.session = session

    def process(
        self, frame: FrameObservation, *, monotonic_time: float, observe: bool = True
    ) -> Any:
        if not observe:
            return SimpleNamespace(snapshot=self.session.snapshot(), detections=())
        adjusted = FrameObservation(
            monotonic_time=monotonic_time,
            motion_score=frame.motion_score,
            detections=frame.detections,
            rejected_count=frame.rejected_count,
            roi_diagonal=frame.roi_diagonal,
            camera_connected=frame.camera_connected,
        )
        return SimpleNamespace(snapshot=self.session.observe(adjusted))

    def camera_disconnected(self, *, monotonic_time: float, observe: bool = True
    ) -> Any:
        return self.session.observe(FrameObservation(
            monotonic_time=monotonic_time,
            motion_score=0,
            camera_connected=False,
        ))

    def reset_frames(self) -> None:
        pass


class FakeCameraProvider:
    def enumerate(self) -> list[Any]:
        return []

    def open(self, device_id: int) -> Any:
        raise AssertionError("unit tests must not open a camera")


class BlockingStream:
    def __init__(self) -> None:
        self.read_started = Event()
        self.release_read = Event()
        self.close_count = 0

    def read(self) -> FrameObservation:
        self.read_started.set()
        self.release_read.wait(timeout=2)
        return FrameObservation(0, 0.0)

    def close(self) -> None:
        self.close_count += 1


class StreamProvider:
    def __init__(self, stream: Any) -> None:
        self.stream = stream

    def enumerate(self) -> list[Any]:
        return []

    def open(self, device_id: int) -> Any:
        return self.stream


class RaisingStream:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.close_count = 0

    def read(self) -> Any:
        raise self.error

    def close(self) -> None:
        self.close_count += 1


class FailingPipeline(ObservationPipeline):
    def process(
        self, frame: FrameObservation, *, monotonic_time: float, observe: bool = True
    ) -> Any:
        raise RuntimeError("pipeline bug")


def prepared_service(
    *, status: GameStatus = GameStatus.IN_PROGRESS,
) -> tuple[RecognitionService, MemoryGameRepository, GameState]:
    repository = MemoryGameRepository()
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"),
        PlayerState("b", "B", "blue"),
    ])
    state.start()
    if status is GameStatus.PAUSED:
        state.pause()
    repository.save_and_set_current(state)
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=1,
        consecutive_value_frames=1,
        candidate_window_frames=1,
    ))
    ticks = count(1)
    service = RecognitionService(
        GameService(repository),
        pipeline=ObservationPipeline(session),
        camera_provider=FakeCameraProvider(),
        clock=lambda: float(next(ticks)),
    )
    return service, repository, state


def drive_candidate(service: RecognitionService, *, detector: float = 1.0) -> dict[str, Any]:
    service.process_frame(FrameObservation(0, 0.0))  # the camera has settled after start
    service.process_frame(FrameObservation(0, 0.1))
    service.process_frame(FrameObservation(0, 0.0))
    return service.process_frame(FrameObservation(
        0,
        0.0,
        detections=(DieObservation(4, detector, (10, 10)),),
        roi_diagonal=100,
    ))


def test_automatic_confirmation_is_registered_once_with_source() -> None:
    service, repository, state = prepared_service()
    snapshot = drive_candidate(service)

    stored = repository.get(state.id)
    assert stored is not None
    assert len(stored.turns) == 1
    assert stored.turns[0].source is RollSource.AUTOMATIC
    assert stored.turns[0].confirmation_id is not None
    assert snapshot["state"] == "removal_wait"

    service.process_frame(FrameObservation(
        0,
        0.0,
        detections=(DieObservation(4, 1.0, (10, 10)),),
        roi_diagonal=100,
    ))
    stored = repository.get(state.id)
    assert stored is not None and len(stored.turns) == 1


def test_paused_game_returns_automatic_result_to_review() -> None:
    service, repository, state = prepared_service(status=GameStatus.PAUSED)
    snapshot = drive_candidate(service)

    stored = repository.get(state.id)
    assert stored is not None and stored.turns == []
    assert snapshot["state"] == "needs_confirmation"
    assert snapshot["candidateId"]
    assert snapshot["errorCode"] == "game_not_in_progress"


@pytest.mark.parametrize(
    ("value", "source"),
    [(None, RollSource.REVIEWED), (6, RollSource.CORRECTED)],
)
def test_candidate_can_be_reviewed_or_corrected(
    value: int | None, source: RollSource
) -> None:
    service, repository, state = prepared_service()
    candidate = drive_candidate(service, detector=0.5)
    result = service.confirm_candidate(
        state.id,
        candidate["candidateId"],
        expected_state_version=state.state_version,
        expected_turn_number=0,
        value=value,
    )

    stored = repository.get(state.id)
    assert stored is not None
    assert stored.turns[0].source is source
    assert stored.turns[0].roll == (value or 4)
    assert result["turns"][0]["source"] == source.value


def test_reviewed_confirmation_is_not_stolen_by_frame_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, state = prepared_service()
    candidate = drive_candidate(service, detector=0.5)
    register = service.game_service.register_recognition_roll

    def interleaved_register(*args: Any, **kwargs: Any) -> GameState:
        service.process_frame(FrameObservation(0, 0.0))
        return register(*args, **kwargs)

    monkeypatch.setattr(
        service.game_service, "register_recognition_roll", interleaved_register
    )
    result = service.confirm_candidate(
        state.id,
        candidate["candidateId"],
        expected_state_version=state.state_version,
        expected_turn_number=0,
    )

    stored = repository.get(state.id)
    assert stored is not None and len(stored.turns) == 1
    assert stored.turns[0].source is RollSource.REVIEWED
    assert result["turnNumber"] == 1
    assert service.snapshot()["state"] == "removal_wait"


def test_confirmation_transition_errors_do_not_escape_as_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, state = prepared_service()
    candidate = drive_candidate(service, detector=0.5)
    assert service.pipeline is not None

    def fail_acknowledge(confirmation_id: str) -> None:
        raise ValueError("forced acknowledgement race")

    monkeypatch.setattr(
        service.pipeline.session, "acknowledge_committed", fail_acknowledge
    )
    result = service.confirm_candidate(
        state.id,
        candidate["candidateId"],
        expected_state_version=state.state_version,
        expected_turn_number=0,
    )
    assert result["turnNumber"] == 1
    assert service.snapshot()["errorCode"] == "recognition_state_conflict"
    stored = repository.get(state.id)
    assert stored is not None and len(stored.turns) == 1

    service2, _, state2 = prepared_service()
    candidate2 = drive_candidate(service2, detector=0.5)
    assert service2.pipeline is not None

    def fail_discard(confirmation_id: str) -> None:
        raise ValueError("forced discard race")

    monkeypatch.setattr(
        service2.pipeline.session, "discard_confirmation", fail_discard
    )
    with pytest.raises(ApiError) as conflict:
        service2.confirm_candidate(
            state2.id,
            candidate2["candidateId"],
            expected_state_version=state2.state_version + 1,
            expected_turn_number=0,
        )
    assert conflict.value.code == "state_version_conflict"


def test_manual_roll_during_cycle_prevents_automatic_second_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, state = prepared_service()
    service.process_frame(FrameObservation(0, 0.1))
    service.process_frame(FrameObservation(0, 0.0))
    register = service.game_service.register_recognition_roll
    inserted = False

    def interleaved_register(*args: Any, **kwargs: Any) -> GameState:
        nonlocal inserted
        if not inserted:
            inserted = True
            with service.game_service.lock:
                current = repository.current()
                assert current is not None
                current.roll(4)
                service.game_service.save_or_503(current)
        return register(*args, **kwargs)

    monkeypatch.setattr(
        service.game_service, "register_recognition_roll", interleaved_register
    )
    snapshot = service.process_frame(FrameObservation(
        0,
        0.0,
        detections=(DieObservation(4, 1.0, (10, 10)),),
        roi_diagonal=100,
    ))

    stored = repository.get(state.id)
    assert stored is not None and len(stored.turns) == 1
    assert stored.turns[0].source is RollSource.MANUAL
    assert snapshot["state"] == "needs_confirmation"
    assert snapshot["errorCode"] == "state_version_conflict"


def test_candidate_confirmation_rejects_a_different_current_game() -> None:
    service, repository, state = prepared_service()
    candidate = drive_candidate(service, detector=0.5)
    replacement = GameState.create(load_board_preset(), [
        PlayerState("c", "C", "green"), PlayerState("d", "D", "yellow")
    ])
    replacement.start()
    assert replacement.state_version == state.state_version
    repository.save_and_set_current(replacement)

    with pytest.raises(ApiError) as conflict:
        service.confirm_candidate(
            state.id,
            candidate["candidateId"],
            expected_state_version=state.state_version,
            expected_turn_number=0,
        )
    assert conflict.value.code == "current_game_conflict"
    current = repository.get(replacement.id)
    assert current is not None and current.turns == []


def test_candidate_reject_and_stale_candidate_conflicts() -> None:
    service, repository, state = prepared_service()
    candidate = drive_candidate(service, detector=0.5)

    with pytest.raises(ApiError) as stale:
        service.reject_candidate("old-candidate")
    assert stale.value.code == "stale_candidate"

    rejected = service.reject_candidate(candidate["candidateId"])
    assert rejected["state"] == "recognition_error"
    stored = repository.get(state.id)
    assert stored is not None and stored.turns == []


@pytest.mark.parametrize(
    ("version_delta", "turn", "code"),
    [(1, 0, "state_version_conflict"), (0, 1, "turn_conflict")],
)
def test_candidate_confirm_rejects_stale_game_context(
    version_delta: int, turn: int, code: str
) -> None:
    service, repository, state = prepared_service()
    candidate = drive_candidate(service, detector=0.5)

    with pytest.raises(ApiError) as conflict:
        service.confirm_candidate(
            state.id,
            candidate["candidateId"],
            expected_state_version=state.state_version + version_delta,
            expected_turn_number=turn,
        )
    assert conflict.value.code == code
    assert service.snapshot()["state"] == "needs_confirmation"
    assert service.snapshot()["candidateId"] == candidate["candidateId"]


def test_confirmation_id_cannot_be_reused_after_undo() -> None:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"), PlayerState("b", "B", "blue")
    ])
    state.start()
    state.roll(4, source=RollSource.AUTOMATIC, confirmation_id="session:1")
    state.undo()

    with pytest.raises(ValueError, match="すでに登録"):
        state.roll(4, source=RollSource.AUTOMATIC, confirmation_id="session:1")


def test_sqlite_restores_recognition_roll_metadata(tmp_path: Path) -> None:
    repository = SQLiteGameRepository(tmp_path / "recognition.sqlite3")
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"), PlayerState("b", "B", "blue")
    ])
    state.start()
    state.roll(
        5,
        source=RollSource.CORRECTED,
        confirmation_id="session:2",
        original_candidate=4,
        confidence={"total": 0.75},
    )
    repository.save(state)

    restored = repository.get(state.id)
    assert restored is not None
    assert restored.turns[0].source is RollSource.CORRECTED
    assert restored.turns[0].confirmation_id == "session:2"
    assert restored.turns[0].original_candidate == 4
    assert restored.turns[0].confidence == {"total": 0.75}


def test_old_snapshot_without_roll_source_defaults_to_manual() -> None:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"), PlayerState("b", "B", "blue")
    ])
    state.start()
    state.roll(2)
    snapshot = game_to_snapshot(state)
    snapshot["turns"][0].pop("source")
    roll_event = next(
        event for event in snapshot["operationEvents"] if event["type"] == "roll"
    )
    roll_event["payload"].pop("source")

    restored = game_from_snapshot(snapshot)
    assert restored.turns[0].source is RollSource.MANUAL
    restored.undo()
    restored.redo()
    assert restored.turns[0].source is RollSource.MANUAL


def test_correcting_recognition_roll_marks_the_rebuilt_turn_corrected() -> None:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"), PlayerState("b", "B", "blue")
    ])
    state.start()
    turn = state.roll(
        4,
        source=RollSource.AUTOMATIC,
        confirmation_id="session:3",
        original_candidate=4,
        confidence={"total": 0.99},
    )
    state.correct_roll(turn.id, 5, reason="誤認識", operator="admin")

    corrected = state.turns[0]
    assert corrected.roll == 5
    assert corrected.source is RollSource.CORRECTED
    assert corrected.original_candidate == 4
    assert corrected.confidence is None
    assert corrected.corrected_from == {
        "value": 4,
        "source": "automatic",
        "confirmationId": "session:3",
        "reason": "誤認識",
        "operator": "admin",
    }
    restored = game_from_snapshot(game_to_snapshot(state))
    assert restored.turns[0].corrected_from == corrected.corrected_from
    roll_event = next(
        event for event in state.operation_events
        if event.active and event.type.value == "roll"
    )
    assert roll_event.payload["correctedFrom"] == {
        "value": 4,
        "source": "automatic",
        "confirmationId": "session:3",
        "reason": "誤認識",
        "operator": "admin",
    }


def test_manual_commit_locks_running_recognition_session() -> None:
    service, _, _ = prepared_service()
    service._running = True
    service.manual_committed(3)
    assert service.snapshot()["state"] == "removal_wait"
    assert service.snapshot()["lastConfirmation"]["source"] == "manual"


def test_subscribers_are_not_notified_for_unchanged_frames() -> None:
    service, _, _ = prepared_service()
    notifications: list[dict[str, Any]] = []
    unsubscribe = service.subscribe(notifications.append)
    service.process_frame(FrameObservation(0, 0.0))
    service.process_frame(FrameObservation(0, 0.0))
    unsubscribe()
    assert len(notifications) == 1


def test_stop_leaves_camera_close_to_worker_and_reports_timeout() -> None:
    service, _, _ = prepared_service()
    stream = BlockingStream()
    service.camera_provider = StreamProvider(stream)
    service.stop_join_timeout = 0.01
    service.start(0)
    assert stream.read_started.wait(timeout=1)
    thread = service._thread
    assert thread is not None

    with pytest.raises(ApiError) as timeout:
        service.stop()
    assert timeout.value.code == "recognition_stop_timeout"
    assert stream.close_count == 0

    stream.release_read.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert stream.close_count == 1
    assert service.running is False


class FakeCameraDisconnected(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("stream_error", "camera_errors", "expected"),
    [
        (FakeCameraDisconnected("gone"), (FakeCameraDisconnected,), "camera_disconnected"),
        (RuntimeError("driver bug"), (), "recognition_worker_failed"),
    ],
)
def test_worker_classifies_camera_and_unexpected_read_failures(
    stream_error: BaseException,
    camera_errors: tuple[type[BaseException], ...],
    expected: str,
) -> None:
    service, _, _ = prepared_service()
    stream = RaisingStream(stream_error)
    service.camera_provider = StreamProvider(stream)
    service.camera_disconnected_errors = camera_errors
    service.start(0)
    thread = service._thread
    if thread is not None:
        thread.join(timeout=1)
    assert service.snapshot()["errorCode"] == expected
    assert stream.close_count == 1


def test_pipeline_failure_is_not_reported_as_camera_disconnection() -> None:
    service, _, _ = prepared_service()
    assert service.pipeline is not None
    service.pipeline = FailingPipeline(service.pipeline.session)
    stream = BlockingStream()
    stream.release_read.set()
    service.camera_provider = StreamProvider(stream)
    service.start(0)
    thread = service._thread
    if thread is not None:
        thread.join(timeout=1)
    assert service.snapshot()["errorCode"] == "recognition_worker_failed"
    assert stream.close_count == 1


def test_start_resets_session_and_handled_confirmation_history_is_bounded() -> None:
    service, _, _ = prepared_service()
    service._running = True
    service.manual_committed(3)
    service._running = False
    assert service.snapshot()["state"] == "removal_wait"
    for index in range(300):
        service._mark_handled(f"confirmation:{index}")
    assert len(service._handled_confirmation_ids) == 256

    stream = BlockingStream()
    service.camera_provider = StreamProvider(stream)
    service.stop_join_timeout = 0.01
    service.start(0)
    assert stream.read_started.wait(timeout=1)
    assert service.snapshot()["state"] == "waiting"
    thread = service._thread
    with pytest.raises(ApiError):
        service.stop()
    stream.release_read.set()
    if thread is not None:
        thread.join(timeout=1)


def test_recognition_http_api_and_manual_roll_source() -> None:
    async def scenario() -> None:
        service, repository, state = prepared_service()
        app = create_app(repository=repository, recognition_service=service)
        candidate = drive_candidate(service, detector=0.5)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            recognition = await client.get("/api/recognition")
            assert recognition.json()["candidateId"] == candidate["candidateId"]
            confirmed = await client.post(
                "/api/recognition/candidate/confirm",
                json={
                    "gameId": state.id,
                    "candidateId": candidate["candidateId"],
                    "expectedStateVersion": state.state_version,
                    "expectedTurnNumber": 0,
                },
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["turns"][0]["source"] == "reviewed"

    asyncio.run(scenario())


def test_server_boots_with_recognition_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "commentator.services.vision.camera", None)
    app = create_app()

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/recognition")
            assert response.status_code == 200
            assert response.json()["state"] == "unavailable"
            assert response.json()["available"] is False

    asyncio.run(scenario())
