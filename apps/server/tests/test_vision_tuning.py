"""Live tuning of the recognition settings from the admin screen."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from threading import Event
from typing import Any

import cv2
import numpy as np
import pytest
from commentator.domain.game import GameState, PlayerState
from commentator.main import MemoryGameRepository, create_app
from commentator.presets import load_board_preset
from commentator.services.game import GameService
from commentator.services.recognition import RecognitionService
from commentator.services.vision.detector import DiceDetector
from commentator.services.vision.pipeline import DiceRecognitionPipeline
from commentator.services.vision.recognition import (
    DiceRecognitionSession,
    RecognitionState,
)
from commentator.services.vision.settings import (
    NormalizedRoi,
    VisionSettings,
    VisionSettingsStore,
)
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).parent / "fixtures" / "dice" / "demo_dice.png"


def _demo(scale: float = 1.0) -> np.ndarray:
    image = cv2.imread(str(FIXTURE))
    assert image is not None
    return np.asarray(image * scale, dtype=np.uint8)


class LoopingStream:
    """Returns the same frame forever and records camera reconfiguration."""

    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.configured: list[dict[str, Any]] = []
        self.closed = False
        self.read_once = Event()

    def read(self) -> np.ndarray:
        time.sleep(0.005)
        self.read_once.set()
        return self.frame

    def configure(self, **values: Any) -> dict[str, Any]:
        self.configured.append(values)
        return {}

    def close(self) -> None:
        self.closed = True


class Provider:
    def __init__(self, stream: LoopingStream) -> None:
        self.stream = stream
        self.opened: list[int] = []

    def enumerate(self) -> list[Any]:
        return []

    def open(self, device_id: int) -> LoopingStream:
        self.opened.append(device_id)
        return self.stream


def _game_service() -> GameService:
    repository = MemoryGameRepository()
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"),
        PlayerState("b", "B", "blue"),
    ])
    repository.save_and_set_current(state)
    return GameService(repository)


def _service(
    tmp_path: Path, frame: np.ndarray | None = None, *, saved: VisionSettings | None = None
) -> tuple[RecognitionService, LoopingStream, Provider, VisionSettingsStore]:
    store = VisionSettingsStore(tmp_path / "vision-settings.json")
    settings = saved or VisionSettings(camera_id=0)
    store.save(settings)
    stream = LoopingStream(frame if frame is not None else _demo())
    provider = Provider(stream)
    pipeline = DiceRecognitionPipeline(
        detector=DiceDetector(
            white_threshold=settings.white_threshold,
            pip_threshold=settings.pip_threshold,
            debug_pips=True,
        ),
        session=DiceRecognitionSession(settings.recognition_config()),
        roi=settings.roi,
    )
    ticks = count(1)
    service = RecognitionService(
        _game_service(),
        pipeline=pipeline,
        camera_provider=provider,
        clock=lambda: float(next(ticks)) * 0.1,
        vision_settings=settings,
        settings_store=store,
    )
    return service, stream, provider, store


@pytest.fixture
def running(tmp_path: Path) -> Iterator[tuple[RecognitionService, LoopingStream, Provider,
                                              VisionSettingsStore]]:
    service, stream, provider, store = _service(tmp_path)
    service.start()
    assert stream.read_once.wait(2)
    _wait_for_frame(service)
    yield service, stream, provider, store
    service.stop()


def _wait_for_frame(service: RecognitionService) -> dict[str, Any]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        diagnostics = service.diagnostics()
        if diagnostics["frameWidth"] is not None:
            return diagnostics
        time.sleep(0.01)
    raise AssertionError("no frame processed")


def test_settings_round_trip_through_the_json_shape() -> None:
    settings = VisionSettings(camera_id=2, white_threshold=None, exposure=-6.0,
                              stationary_frames=4)
    assert VisionSettings.from_dict(settings.to_dict()) == settings
    changed = settings.with_changes({"roi": {"x": 0.1, "width": 0.5}, "pipThreshold": 200})
    assert changed.roi == NormalizedRoi(x=0.1, y=0.0, width=0.5, height=1.0)
    assert changed.pip_threshold == 200
    with pytest.raises(ValueError):
        settings.with_changes({"unknown": 1})
    with pytest.raises(ValueError):
        settings.with_changes({"roi": {"depth": 1}})
    with pytest.raises(ValueError):
        settings.with_changes({"roi": {"x": 0.8, "width": 0.5}})  # outside the frame


def test_update_applies_at_once_but_saves_only_on_request(
    running: tuple[RecognitionService, LoopingStream, Provider, VisionSettingsStore],
) -> None:
    service, _stream, _provider, store = running
    before = store.path.read_text(encoding="utf-8")
    payload = service.update_vision_settings({
        "whiteThreshold": 180, "pipThreshold": None,
        "roi": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
    })
    assert payload["unsaved"] is True
    assert payload["settings"]["whiteThreshold"] == 180
    assert payload["saved"]["whiteThreshold"] == 230
    pipeline = service.pipeline
    assert isinstance(pipeline, DiceRecognitionPipeline)
    assert isinstance(pipeline.detector, DiceDetector)
    assert pipeline.detector.white_threshold == 180
    assert pipeline.detector.pip_threshold is None
    assert pipeline.roi == NormalizedRoi(0.1, 0.1, 0.8, 0.8)
    assert store.path.read_text(encoding="utf-8") == before

    saved = service.save_vision_settings()
    assert saved["unsaved"] is False
    assert json.loads(store.path.read_text(encoding="utf-8"))["whiteThreshold"] == 180

    service.update_vision_settings({"whiteThreshold": 100})
    reverted = service.revert_vision_settings()
    assert reverted["settings"]["whiteThreshold"] == 180
    assert pipeline.detector.white_threshold == 180

    defaults = service.revert_vision_settings(defaults=True)
    assert defaults["settings"] == VisionSettings().to_dict()
    assert defaults["unsaved"] is True


def test_decision_thresholds_wait_for_the_current_throw(tmp_path: Path) -> None:
    service, _stream, _provider, _store = _service(tmp_path)
    session = service.pipeline.session if service.pipeline else None
    assert session is not None
    session.state = RecognitionState.MOVING
    payload = service.update_vision_settings({"stationaryFrames": 3})
    assert payload["pendingRecognitionSettings"] is True
    assert session.config.stationary_frames != 3
    session.state = RecognitionState.WAITING
    service._apply_pending_config_locked()
    assert session.config.stationary_frames == 3
    assert service.vision_settings_payload()["pendingRecognitionSettings"] is False


def test_camera_controls_are_applied_by_the_worker(
    running: tuple[RecognitionService, LoopingStream, Provider, VisionSettingsStore],
) -> None:
    service, stream, _provider, _store = running
    assert len(stream.configured) == 1  # at start
    service.update_vision_settings({"exposure": -5.0,
                                    "cameraResolution": {"width": 640, "height": 480}})
    deadline = time.monotonic() + 2
    while len(stream.configured) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stream.configured[-1] == {"width": 640, "height": 480, "exposure": -5.0}


def test_changing_the_camera_restarts_recognition(
    running: tuple[RecognitionService, LoopingStream, Provider, VisionSettingsStore],
) -> None:
    service, _stream, provider, _store = running
    service.update_vision_settings({"cameraId": 3})
    assert provider.opened == [0, 3]
    assert service.running


def test_diagnostics_and_preview_views(
    running: tuple[RecognitionService, LoopingStream, Provider, VisionSettingsStore],
) -> None:
    service, _stream, _provider, _store = running
    diagnostics = _wait_for_frame(service)
    assert diagnostics["frameWidth"] == 1000
    assert [item["value"] for item in diagnostics["detections"]] == [1, 4, 6, 5, 3]
    assert diagnostics["whiteThreshold"] == {"mode": "fixed", "applied": 230.0}
    assert any("2個以上" in hint for hint in diagnostics["hints"])
    assert any("1280x720" in hint for hint in diagnostics["hints"])
    for view in ("overlay", "mask", "pips"):
        assert service.preview_jpeg(view).startswith(b"\xff\xd8")


def test_hint_when_the_white_threshold_is_above_the_brightest_pixel(tmp_path: Path) -> None:
    dim = _demo(0.5)
    service, stream, _provider, _store = _service(tmp_path, dim)
    service.start()
    try:
        assert stream.read_once.wait(2)
        diagnostics = _wait_for_frame(service)
        assert diagnostics["detections"] == []
        assert any("外形しきい値" in hint and "下げる" in hint
                   for hint in diagnostics["hints"])
        service.update_vision_settings({"whiteThreshold": None})
        time.sleep(0.1)
        assert service.diagnostics()["whiteThreshold"]["mode"] == "auto"
    finally:
        service.stop()


def test_api_validates_and_reports(tmp_path: Path) -> None:
    service, _stream, _provider, _store = _service(tmp_path)
    client = TestClient(create_app(recognition_service=service))

    response = client.get("/api/settings/vision")
    assert response.status_code == 200
    assert response.json()["unsaved"] is False

    for body in (
        {"unknown": 1},
        {"stationaryFrames": None},
        {"whiteThreshold": 300},
        {"automaticThreshold": 0.6, "confirmationThreshold": 0.7},
        {"roi": {"x": 0.9, "width": 0.5}},
        {"cameraResolution": {"width": 123, "height": 45}},
    ):
        assert client.put("/api/settings/vision", json=body).status_code == 422, body

    response = client.put("/api/settings/vision", json={"whiteThreshold": None,
                                                      "roi": {"x": 0.2, "width": 0.5}})
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["whiteThreshold"] is None
    assert body["settings"]["roi"]["x"] == 0.2
    assert body["unsaved"] is True

    assert client.post("/api/settings/vision/save").json()["unsaved"] is False
    reverted = client.post("/api/settings/vision/revert", json={"target": "defaults"})
    assert reverted.json()["settings"]["whiteThreshold"] == 230

    diagnostics = client.get("/api/recognition/diagnostics")
    assert diagnostics.status_code == 200
    assert diagnostics.json()["running"] is False


def test_api_reports_unavailable_recognition() -> None:
    service = RecognitionService(_game_service(), pipeline=None, camera_provider=None)
    client = TestClient(create_app(recognition_service=service))
    assert client.get("/api/settings/vision").status_code == 503
    assert client.put("/api/settings/vision", json={"pipThreshold": 200}).status_code == 503
