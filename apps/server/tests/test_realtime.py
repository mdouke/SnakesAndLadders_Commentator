from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from itertools import count
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from commentator.api.responses import REALTIME_MESSAGE_ADAPTER
from commentator.main import MemoryGameRepository, create_app
from commentator.services.commentary import CommentaryService
from commentator.services.game import GameService
from commentator.services.realtime import RealtimeHub
from commentator.services.recognition import RecognitionService
from commentator.services.vision.detector import DieDetection
from commentator.services.vision.recognition import (
    DiceRecognitionSession,
    DieObservation,
    FrameObservation,
    RecognitionConfig,
)
from commentator.services.vision.settings import NormalizedRoi
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

PLAYERS = {"players": [{"name": "A", "color": "red"}, {"name": "B", "color": "blue"}]}


class ObservationPipeline:
    """Feeds FrameObservation objects straight into the session."""

    def __init__(self, session: DiceRecognitionSession) -> None:
        self.session = session

    def process(
        self, frame: FrameObservation, *, monotonic_time: float, observe: bool = True
    ) -> Any:
        if not observe:
            return SimpleNamespace(snapshot=self.session.snapshot(), detections=())
        return SimpleNamespace(snapshot=self.session.observe(FrameObservation(
            monotonic_time=monotonic_time,
            motion_score=frame.motion_score,
            detections=frame.detections,
            rejected_count=frame.rejected_count,
            roi_diagonal=frame.roi_diagonal,
        )), detections=())

    def camera_disconnected(self, *, monotonic_time: float, observe: bool = True
    ) -> Any:
        return self.session.observe(FrameObservation(
            monotonic_time=monotonic_time, motion_score=0, camera_connected=False,
        ))

    def reset_frames(self) -> None:
        pass


class NoCamera:
    def enumerate(self) -> list[Any]:
        return []

    def open(self, device_id: int) -> Any:
        raise AssertionError("this test must not open a camera")


def make_client() -> tuple[TestClient, RecognitionService, MemoryGameRepository]:
    repository = MemoryGameRepository()
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=1, consecutive_value_frames=1, candidate_window_frames=1,
    ))
    ticks = count(1)
    service = RecognitionService(
        GameService(repository),
        pipeline=ObservationPipeline(session),
        camera_provider=NoCamera(),
        clock=lambda: float(next(ticks)),
    )
    commentary = CommentaryService(
        GameService(repository), scheduler=lambda job: None  # never generates
    )
    app = create_app(
        repository=repository, recognition_service=service, commentary_service=commentary
    )
    return TestClient(app), service, repository


@pytest.fixture
def client() -> Iterator[tuple[TestClient, RecognitionService]]:
    test_client, service, _ = make_client()
    with test_client:
        yield test_client, service


def receive(ws: WebSocketTestSession) -> dict[str, Any]:
    message: dict[str, Any] = ws.receive_json()
    REALTIME_MESSAGE_ADAPTER.validate_python(message)  # matches the published schema
    return message


def initial_messages(ws: WebSocketTestSession) -> dict[str, dict[str, Any]]:
    messages = [receive(ws) for _ in range(4)]
    sequences = [message["sequence"] for message in messages]
    assert sequences == sorted(sequences)
    return {message["type"]: message for message in messages}


def receive_until(
    ws: WebSocketTestSession, predicate: Any, *, limit: int = 30
) -> list[dict[str, Any]]:
    received: list[dict[str, Any]] = []
    for _ in range(limit):
        message = receive(ws)
        received.append(message)
        if predicate(message):
            return received
    raise AssertionError(f"expected message not received: {received}")


def test_connect_sends_game_recognition_and_commentary_snapshots(
    client: tuple[TestClient, RecognitionService],
) -> None:
    http, _ = client
    with http.websocket_connect("/ws") as ws:
        initial = initial_messages(ws)
    assert initial["game"]["payload"] is None
    assert initial["recognition"]["payload"]["state"] == "waiting"
    assert initial["commentary"]["payload"]["status"] == "idle"
    assert initial["commentary"]["payload"]["text"] is None
    assert initial["speech"]["payload"]["status"] == "idle"


def test_two_clients_receive_the_same_game_updates_in_the_same_order(
    client: tuple[TestClient, RecognitionService],
) -> None:
    http, _ = client
    with http.websocket_connect("/ws") as first, http.websocket_connect("/ws") as second:
        initial_messages(first)
        initial_messages(second)
        game = http.post("/api/games", json=PLAYERS).json()
        started = http.post(
            f"/api/games/{game['id']}/start", json={"expectedStateVersion": 0}
        ).json()
        http.post(
            f"/api/games/{game['id']}/rolls",
            json={"value": 3, "expectedStateVersion": started["stateVersion"]},
        )
        streams = []
        for ws in (first, second):
            messages = [receive(ws) for _ in range(3)]
            streams.append([
                (message["sequence"], message["payload"]["stateVersion"])
                for message in messages
            ])
            assert all(message["type"] == "game" for message in messages)
    assert streams[0] == streams[1]
    assert [version for _, version in streams[0]] == [0, 1, 2]


def test_automatic_confirmation_broadcasts_recognition_and_game(
    client: tuple[TestClient, RecognitionService],
) -> None:
    http, service = client
    game = http.post("/api/games", json=PLAYERS).json()
    http.post(f"/api/games/{game['id']}/start", json={"expectedStateVersion": 0})
    with http.websocket_connect("/ws") as ws:
        initial_messages(ws)
        # The camera settles after start; the die-free tray waits for a throw.
        service.process_frame(FrameObservation(0, 0.0))
        receive_until(
            ws,
            lambda message: message["type"] == "recognition"
            and message["payload"]["state"] == "removal_wait",
        )
        service.process_frame(FrameObservation(0, 0.1))
        service.process_frame(FrameObservation(0, 0.0))
        service.process_frame(FrameObservation(
            0, 0.0, detections=(DieObservation(4, 1.0, (10, 10)),), roi_diagonal=100,
        ))
        received = receive_until(
            ws,
            lambda message: message["type"] == "recognition"
            and message["payload"]["state"] == "removal_wait",
        )
    games = [message for message in received if message["type"] == "game"]
    assert len(games) == 1
    assert games[0]["payload"]["turns"][0]["source"] == "automatic"
    states = [
        message["payload"]["state"] for message in received
        if message["type"] == "recognition"
    ]
    assert states[0] == "moving"
    assert states[-1] == "removal_wait"
    # The game update is queued while the recognition lock is held, before the
    # recognition state that acknowledges it.
    assert received.index(games[0]) < len(received) - 1


def test_reconnect_receives_latest_state_first(
    client: tuple[TestClient, RecognitionService],
) -> None:
    http, _ = client
    with http.websocket_connect("/ws") as ws:
        initial_messages(ws)
    game = http.post("/api/games", json=PLAYERS).json()
    http.post(f"/api/games/{game['id']}/start", json={"expectedStateVersion": 0})
    with http.websocket_connect("/ws") as ws:
        initial = initial_messages(ws)
    assert initial["game"]["payload"]["id"] == game["id"]
    assert initial["game"]["payload"]["status"] == "in_progress"
    assert http.app.state.realtime_hub.client_count == 0  # type: ignore[attr-defined]


def test_activating_another_game_broadcasts_it(
    client: tuple[TestClient, RecognitionService],
) -> None:
    http, _ = client
    first = http.post("/api/games", json=PLAYERS).json()
    http.post("/api/games", json=PLAYERS)
    with http.websocket_connect("/ws") as ws:
        initial_messages(ws)
        http.post(f"/api/games/{first['id']}/activate", json={"expectedStateVersion": 0})
        message = receive(ws)
    assert message["type"] == "game"
    assert message["payload"]["id"] == first["id"]


def test_slow_client_is_closed_instead_of_blocking_others() -> None:
    async def scenario() -> list[Any]:
        hub = RealtimeHub(max_pending=2)
        slow = hub.connect()
        for value in range(3):
            hub.publish("commentary", {"value": value})
        await asyncio.sleep(0)
        drained = []
        while not slow.queue.empty():
            drained.append(slow.queue.get_nowait())
        return [slow.closed, drained]

    closed, drained = asyncio.run(scenario())
    assert closed is True
    assert drained == [None]


class FrameStream:
    def __init__(self) -> None:
        self.closed = False

    def read(self) -> np.ndarray:
        time.sleep(0.005)
        return np.zeros((120, 160, 3), dtype=np.uint8)

    def close(self) -> None:
        self.closed = True


class FrameCamera:
    def __init__(self) -> None:
        self.stream = FrameStream()

    def enumerate(self) -> list[Any]:
        return [SimpleNamespace(id=0, label="fake")]

    def open(self, device_id: int) -> FrameStream:
        return self.stream


class FramePipeline:
    def __init__(self) -> None:
        self.session = DiceRecognitionSession()
        self.roi = NormalizedRoi(x=0.25, y=0.25, width=0.5, height=0.5)

    def process(
        self, frame: np.ndarray, *, monotonic_time: float, observe: bool = True
    ) -> Any:
        detection = DieDetection(
            value=5,
            box=np.array([[0, 0], [20, 0], [20, 20], [0, 20]], dtype=np.int32),
            center=(10, 10),
            confidence=0.9,
            pip_centers=(),
        )
        snapshot = (
            self.session.observe(FrameObservation(monotonic_time, 0.0))
            if observe else self.session.snapshot()
        )
        return SimpleNamespace(snapshot=snapshot, detections=(detection,))

    def camera_disconnected(self, *, monotonic_time: float, observe: bool = True
    ) -> Any:
        return self.session.observe(FrameObservation(
            monotonic_time=monotonic_time, motion_score=0, camera_connected=False,
        ))

    def reset_frames(self) -> None:
        pass


def test_preview_returns_jpeg_only_while_recognition_runs() -> None:
    repository = MemoryGameRepository()
    camera = FrameCamera()
    service = RecognitionService(
        GameService(repository), pipeline=FramePipeline(), camera_provider=camera,
    )
    with TestClient(create_app(repository=repository, recognition_service=service)) as http:
        stopped = http.get("/api/recognition/preview.jpg")
        assert stopped.status_code == 409
        assert stopped.json()["detail"]["code"] == "recognition_preview_unavailable"

        assert http.post("/api/recognition/start", json={"cameraId": 0}).status_code == 200
        deadline = time.monotonic() + 2.0
        response = http.get("/api/recognition/preview.jpg")
        while response.status_code != 200 and time.monotonic() < deadline:
            time.sleep(0.01)
            response = http.get("/api/recognition/preview.jpg")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        assert response.content[:2] == b"\xff\xd8"

        http.post("/api/recognition/stop")
        assert camera.stream.closed
        assert http.get("/api/recognition/preview.jpg").status_code == 409
