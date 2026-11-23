"""Automatic tray detection: outline, tracking, masking and the image sent to Claude."""

from __future__ import annotations

import math
import time
from typing import Any

import cv2
import numpy as np
from commentator.services.game import GameService
from commentator.services.recognition import RecognitionService
from commentator.services.vision.detector import DiceDetector
from commentator.services.vision.motion import Roi
from commentator.services.vision.pipeline import DiceRecognitionPipeline
from commentator.services.vision.recognition import (
    DiceRecognitionSession,
    RecognitionConfig,
    RecognitionState,
)
from commentator.services.vision.settings import NormalizedRoi, VisionSettings
from commentator.services.vision.tray import TrayShape, TrayTracker, detect_tray, shape_iou

from .test_vision_tuning import LoopingStream, Provider, _game_service

WIDTH, HEIGHT = 1280, 720
TABLE, TRAY, SHADOW = 205, 18, 120
# Search area like the operator's screenshot: the tray plus table corners.
SEARCH = NormalizedRoi(0.31, 0.06, 0.38, 0.70)


def hexagon(center: tuple[int, int], radius: int) -> np.ndarray:
    """Pointy-top hexagon, as in the real tray."""
    cx, cy = center
    return np.array([
        [round(cx + radius * math.cos(math.radians(90 + 60 * i))),
         round(cy - radius * math.sin(math.radians(90 + 60 * i)))]
        for i in range(6)
    ], dtype=np.int32)


def scene(center: tuple[int, int] = (640, 290), *, die: bool = True,
          die_at: tuple[int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    frame = np.full((HEIGHT, WIDTH, 3), TABLE, dtype=np.uint8)
    tray = hexagon(center, 240)
    # Shadow down-left of the tray, then two dark stand legs touching it.
    cv2.fillPoly(frame, [tray + np.array([-40, 40])], (SHADOW,) * 3)
    cv2.fillPoly(frame, [tray], (TRAY,) * 3)
    cv2.line(frame, (center[0] + 200, center[1] + 140), (center[0] + 330, center[1] + 450),
             (25, 25, 25), 24)
    cv2.line(frame, (center[0] - 260, center[1] + 200), (center[0] - 180, center[1] + 450),
             (25, 25, 25), 24)
    if die:
        x, y = die_at or (center[0] + 20, center[1] + 70)
        cv2.rectangle(frame, (x - 40, y - 40), (x + 40, y + 40), (225, 225, 225), -1)
        for dx, dy in ((-18, -18), (18, 18), (-18, 18), (18, -18)):  # four pips
            cv2.circle(frame, (x + dx, y + dy), 8, (10, 10, 10), -1)
    noise = np.random.default_rng(1).integers(-4, 5, frame.shape)
    return np.clip(frame.astype(int) + noise, 0, 255).astype(np.uint8), tray


def truth(tray: np.ndarray) -> TrayShape:
    return TrayShape(tuple((float(x) / WIDTH, float(y) / HEIGHT) for x, y in tray), 0.0)


def search_region(frame: np.ndarray) -> Roi:
    return SEARCH.to_pixels(frame)


def test_detects_the_hexagon_and_ignores_shadow_and_legs() -> None:
    frame, tray = scene()
    region = search_region(frame)
    found = detect_tray(region.crop(frame), region, (WIDTH, HEIGHT))
    assert found is not None
    assert 5 <= len(found.points) <= 9
    # The search area cuts the top and bottom points; compare inside it only.
    clipped = TrayShape(tuple(
        (min(max(x, SEARCH.x), SEARCH.x + SEARCH.width),
         min(max(y, SEARCH.y), SEARCH.y + SEARCH.height))
        for x, y in truth(tray).points
    ), 0.0)
    assert shape_iou(found, clipped) > 0.9


def test_no_tray_means_no_outline() -> None:
    frame = np.full((HEIGHT, WIDTH, 3), TABLE, dtype=np.uint8)
    region = search_region(frame)
    assert detect_tray(region.crop(frame), region, (WIDTH, HEIGHT)) is None


def test_tracker_adopts_stable_outlines_and_follows_a_moved_tray() -> None:
    tracker = TrayTracker()
    first, _ = scene()
    region = search_region(first)

    def observe(frame: np.ndarray) -> bool:
        return tracker.observe(region.crop(frame), region, (WIDTH, HEIGHT), now=time.monotonic())

    assert [observe(first) for _ in range(3)] == [False, False, True]
    original = tracker.shape
    moved, _ = scene((690, 300))
    assert observe(moved) is False  # one odd frame is not enough
    assert observe(first) is False
    assert tracker.shape == original
    assert [observe(moved) for _ in range(3)] == [False, False, True]
    assert tracker.shape is not None and original is not None
    assert shape_iou(tracker.shape, original) < 0.92



def test_tracker_follows_a_small_shift_but_not_jitter() -> None:
    # 2026-11-23: shifts up to ~20 px were taken as "not moved", and a die
    # lying near the rim of the shifted tray was masked out.
    tracker = TrayTracker()
    first, _ = scene()
    region = search_region(first)

    def observe(frame: np.ndarray) -> bool:
        return tracker.observe(region.crop(frame), region, (WIDTH, HEIGHT), now=time.monotonic())

    assert [observe(first) for _ in range(3)] == [False, False, True]
    original = tracker.shape
    nudged, _ = scene((641, 290))  # one pixel: detection noise, not a move
    assert [observe(nudged) for _ in range(3)] == [False, False, False]
    assert tracker.shape == original
    shifted, _ = scene((652, 290))  # 12 px
    assert [observe(shifted) for _ in range(3)] == [False, False, True]
    assert tracker.shape != original


def test_outline_is_followed_after_a_recognition_error() -> None:
    frame, _ = scene()
    tracked = pipeline(tray_detection=True)
    for index in range(3):
        tracked.process(frame, monotonic_time=float(index))
    adopted = tracked.tray.shape
    tracked.session.state = RecognitionState.RECOGNITION_ERROR
    moved, _ = scene((700, 300))
    for index in range(40):
        # observe=False keeps the session in its error state for this check.
        tracked.process(moved, monotonic_time=10.0 + index, observe=False)
    assert tracked.session.state is RecognitionState.RECOGNITION_ERROR
    assert tracked.tray.shape != adopted

def pipeline(**kwargs: Any) -> DiceRecognitionPipeline:
    return DiceRecognitionPipeline(
        detector=DiceDetector(white_threshold=180, pip_threshold=120),
        session=DiceRecognitionSession(RecognitionConfig(stationary_frames=3)),
        roi=SEARCH,
        **kwargs,
    )


def test_masked_crop_hides_the_table_and_keeps_the_die() -> None:
    frame, _ = scene()
    plain = pipeline()
    result = plain.process(frame, monotonic_time=0.0)
    # Without the tray outline, the bright table corners swamp the white mask.
    assert float((result.white_mask > 0).mean()) > 0.1

    tracked = pipeline(tray_detection=True)
    for index in range(4):
        result = tracked.process(frame, monotonic_time=float(index))
    assert tracked.tray.shape is not None
    assert [item.value for item in result.detections] == [4]
    assert float((result.white_mask > 0).mean()) < 0.05
    crop = tracked.crop(frame)
    assert crop[0, 0].tolist() == [0, 0, 0]  # a corner outside the tray
    bounds, mask = tracked.region(frame)
    assert mask is not None and mask.shape == (bounds.height, bounds.width)


def test_outline_is_not_updated_during_a_throw() -> None:
    frame, _ = scene()
    tracked = pipeline(tray_detection=True)
    for index in range(3):
        tracked.process(frame, monotonic_time=float(index))
    adopted = tracked.tray.shape
    tracked.session.state = RecognitionState.MOVING
    moved, _ = scene((700, 300))
    for index in range(40):
        tracked.process(moved, monotonic_time=10.0 + index)
    assert tracked.tray.shape == adopted


def test_service_sends_the_masked_tray_and_reports_it(tmp_path: Any) -> None:
    frame, _ = scene()
    settings = VisionSettings(camera_id=0, roi=SEARCH, white_threshold=180, pip_threshold=120,
                              tray_detection=True)
    stream = LoopingStream(frame)
    service = RecognitionService(
        _game_service(), pipeline=pipeline(tray_detection=True),
        camera_provider=Provider(stream), vision_settings=settings,
    )
    service.start()
    try:
        deadline = time.monotonic() + 3
        while not service.diagnostics()["tray"]["found"]:
            assert time.monotonic() < deadline, "tray not found"
            time.sleep(0.02)
        diagnostics = service.diagnostics()
        assert diagnostics["tray"]["vertices"] >= 5
        # Brightness is measured inside the tray: the die, not the table.
        assert diagnostics["roiBrightness"]["p99"] > TABLE
        assert diagnostics["whiteAreaRatio"] < 0.05
        sent = service._crop(frame)
        assert sent[0, 0].tolist() == [0, 0, 0]
        assert service.preview_jpeg("overlay").startswith(b"\xff\xd8")

        service.update_vision_settings({"trayDetection": False})
        assert service.diagnostics()["tray"] == {
            "enabled": False, "found": False, "lastAttemptFound": None,
            "vertices": None, "areaRatio": None, "secondsSinceUpdate": None,
        }
    finally:
        service.stop()


def test_game_service_is_not_needed_for_detection() -> None:
    assert isinstance(_game_service(), GameService)
