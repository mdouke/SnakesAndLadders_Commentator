from pathlib import Path

import cv2
import numpy as np
import pytest
from commentator.services.vision.detector import DiceDetector, DieDetection
from commentator.services.vision.motion import Roi, frame_motion_score
from commentator.services.vision.pipeline import DiceRecognitionPipeline
from commentator.services.vision.recognition import (
    ConfirmationSource,
    DiceRecognitionSession,
    DieObservation,
    FrameObservation,
    RecognitionConfig,
    RecognitionState,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dice"
PIPS = {
    1: [(0, 0)],
    2: [(-1, -1), (1, 1)],
    3: [(-1, -1), (0, 0), (1, 1)],
    4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
    5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
    6: [(-1, -1), (-1, 0), (-1, 1), (1, -1), (1, 0), (1, 1)],
}


def frame(
    time: float,
    motion: float,
    *,
    value: int | None = None,
    confidence: float = 0.98,
    center: tuple[float, float] = (50.0, 50.0),
    connected: bool = True,
    rejected_count: int = 0,
) -> FrameObservation:
    detections = (
        (DieObservation(value, confidence, center),) if value is not None else ()
    )
    return FrameObservation(
        monotonic_time=time,
        motion_score=motion,
        detections=detections,
        rejected_count=rejected_count,
        roi_diagonal=100.0,
        camera_connected=connected,
    )


def ready_session(*, detector_confidence: float = 0.98) -> DiceRecognitionSession:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        consecutive_value_frames=3,
        removal_frames=2,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.01))
    session.observe(frame(2, 0.01))
    assert session.state is RecognitionState.STATIONARY
    for timestamp in (3, 4):
        session.observe(frame(timestamp, 0.0, value=4, confidence=detector_confidence))
    assert session.snapshot().state is RecognitionState.STATIONARY
    return session


def test_saved_demo_image_detects_expected_dice_values() -> None:
    image = cv2.imread(str(FIXTURES / "demo_dice.png"))
    assert image is not None
    detections, mask, rejected_count = DiceDetector().detect(image)
    assert sorted(detection.value for detection in detections) == [1, 3, 4, 5, 6]
    assert mask.shape == image.shape[:2]
    assert rejected_count == 0


def test_detector_handles_all_values_and_rotations() -> None:
    canvas = np.zeros((700, 1050, 3), dtype=np.uint8)
    specs = [
        ((150, 150), 120, 1, -8),
        ((500, 150), 130, 2, 9),
        ((870, 150), 125, 3, -4),
        ((150, 500), 130, 4, 7),
        ((500, 500), 125, 5, -10),
        ((870, 500), 130, 6, 5),
    ]
    for center, size, value, angle in specs:
        _draw_die(canvas, center, size, value, angle)
    detected, _, rejected_count = DiceDetector().detect(canvas)
    assert sorted(die.value for die in detected) == [1, 2, 3, 4, 5, 6]
    assert rejected_count == 0


def test_detector_reports_die_shape_with_invalid_pip_count() -> None:
    canvas = np.zeros((300, 300, 3), dtype=np.uint8)
    cv2.rectangle(canvas, (90, 90), (210, 210), (255, 255, 255), -1)
    detected, _, rejected_count = DiceDetector().detect(canvas)
    assert detected == []
    assert rejected_count == 1


def test_motion_score_and_roi_validation() -> None:
    previous = np.zeros((10, 10, 3), dtype=np.uint8)
    current = np.full((10, 10, 3), 255, dtype=np.uint8)
    assert frame_motion_score(previous, previous) == 0
    assert frame_motion_score(previous, current) == pytest.approx(1.0)
    assert Roi(2, 3, 4, 5).crop(current).shape == (5, 4, 3)
    with pytest.raises(ValueError, match="範囲外"):
        Roi(8, 8, 4, 4).crop(current)


def test_automatic_confirmation_is_locked_until_removal() -> None:
    session = ready_session()
    result = session.observe(frame(5, 0.0, value=4))
    assert result.state is RecognitionState.CONFIRMED
    assert result.confirmation is not None
    assert result.confirmation.value == 4
    assert result.confirmation.source is ConfirmationSource.AUTOMATIC
    assert result.confidence is not None and result.confidence.total >= 0.85

    confirmation_id = result.confirmation.id
    session.acknowledge_committed(confirmation_id)
    for timestamp in (6, 7, 8):
        assert session.observe(frame(timestamp, 0.0, value=4)).confirmation is not None
        assert session.state is RecognitionState.REMOVAL_WAIT
    session.observe(frame(9, 0.0))
    session.observe(frame(10, 0.0))
    assert session.state is RecognitionState.WAITING


def test_low_confidence_candidate_can_be_corrected_by_operator() -> None:
    session = ready_session(detector_confidence=0.20)
    result = session.observe(frame(5, 0.0, value=4, confidence=0.20))
    assert result.state is RecognitionState.NEEDS_CONFIRMATION
    assert result.confidence is not None
    assert 0.60 <= result.confidence.total < 0.85

    confirmation = session.confirm_candidate(value=5, monotonic_time=6)
    assert confirmation.value == 5
    assert confirmation.original_candidate == 4
    assert confirmation.source is ConfirmationSource.CORRECTED


def test_motion_unlocks_confirmed_die_as_a_new_roll() -> None:
    session = ready_session()
    confirmed = session.observe(frame(5, 0.0, value=4)).confirmation
    assert confirmed is not None
    session.acknowledge_committed(confirmed.id)
    session.observe(frame(6, 0.2, value=4))
    assert session.state is RecognitionState.MOVING


def test_camera_disconnect_requires_safe_recovery() -> None:
    session = DiceRecognitionSession()
    failed = session.observe(frame(0, 0.0, connected=False))
    assert failed.state is RecognitionState.CAMERA_ERROR
    assert failed.error_code == "camera_disconnected"
    recovered = session.observe(frame(1, 0.0))
    assert recovered.state is RecognitionState.WAITING


def test_recognition_timeout_allows_manual_recovery() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        recognition_timeout_seconds=2,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.1, value=6))
    timed_out = session.observe(frame(2, 0.1, value=6))
    assert timed_out.state is RecognitionState.RECOGNITION_ERROR
    assert timed_out.error_code == "recognition_timeout"
    restarted = session.observe(frame(2.05, 0.2, value=6))
    assert restarted.state is RecognitionState.MOVING
    assert restarted.error_code is None
    manual = session.enter_manual(6, monotonic_time=2.1)
    assert manual.value == 6
    assert manual.source is ConfirmationSource.MANUAL


def test_unreadable_die_times_out_instead_of_returning_to_waiting() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        removal_frames=2,
        recognition_timeout_seconds=3,
    ))
    session.observe(frame(0, 0.2, rejected_count=1))
    session.observe(frame(1, 0.0, rejected_count=1))
    session.observe(frame(2, 0.0, rejected_count=1))
    session.observe(frame(2.5, 0.0, rejected_count=1))
    result = session.observe(frame(3, 0.0, rejected_count=1))
    assert result.state is RecognitionState.RECOGNITION_ERROR
    assert result.error_code == "recognition_timeout"


def test_empty_stationary_area_returns_to_waiting() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        removal_frames=2,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.0))
    session.observe(frame(2, 0.0))
    assert session.state is RecognitionState.STATIONARY
    session.observe(frame(3, 0.0))
    result = session.observe(frame(4, 0.0))
    assert result.state is RecognitionState.WAITING


def test_below_threshold_candidate_is_exposed_as_discarded() -> None:
    session = ready_session(detector_confidence=0.0)
    result = session.observe(frame(5, 0.0, value=4, confidence=0.0))
    assert result.state is RecognitionState.STATIONARY
    assert result.discarded_candidate is not None
    assert result.discarded_candidate.value == 4
    assert result.discarded_candidate.confidence.total < 0.60


def test_observation_time_must_be_monotonic() -> None:
    session = DiceRecognitionSession()
    session.observe(frame(2, 0.0))
    with pytest.raises(ValueError, match="単調増加"):
        session.observe(frame(1, 0.0))


def test_removing_die_with_motion_returns_to_waiting_without_timeout() -> None:
    session = ready_session()
    confirmed = session.observe(frame(5, 0.0, value=4)).confirmation
    assert confirmed is not None
    session.acknowledge_committed(confirmed.id)
    session.observe(frame(6, 0.15))
    assert session.state is RecognitionState.MOVING
    session.observe(frame(7, 0.0))
    stationary = session.observe(frame(8, 0.0))
    assert stationary.state is RecognitionState.STATIONARY
    session.observe(frame(9, 0.0))
    waiting = session.observe(frame(10, 0.0))
    assert waiting.state is RecognitionState.WAITING
    for timestamp in range(11, 25):
        result = session.observe(frame(float(timestamp), 0.0))
        assert result.state is RecognitionState.WAITING
        assert result.error_code is None


def test_candidate_is_evaluated_on_the_frame_that_completes_the_sequence() -> None:
    session = ready_session()
    result = session.observe(frame(5, 0.0, value=4))
    assert result.state is RecognitionState.CONFIRMED
    interrupted = session.observe(frame(6, 0.5))
    assert interrupted.state is RecognitionState.CONFIRMED
    assert interrupted.confirmation is not None
    assert interrupted.confirmation.value == 4


def test_rejected_candidate_waits_for_movement_or_manual_input() -> None:
    session = ready_session(detector_confidence=0.20)
    assert session.observe(
        frame(5, 0.0, value=4, confidence=0.20)
    ).state is RecognitionState.NEEDS_CONFIRMATION
    session.reject_candidate()
    for timestamp in (6, 7, 8, 9, 10):
        result = session.observe(
            frame(timestamp, 0.0, value=4, confidence=0.20)
        )
        assert result.state is RecognitionState.RECOGNITION_ERROR
        assert result.error_code == "candidate_rejected"
    restarted = session.observe(frame(11, 0.2, value=4, confidence=0.20))
    assert restarted.state is RecognitionState.MOVING
    assert restarted.error_code is None


def test_failed_game_registration_can_discard_and_correct_confirmation() -> None:
    session = ready_session()
    automatic = session.observe(frame(5, 0.0, value=4)).confirmation
    assert automatic is not None
    session.discard_confirmation(automatic.id)
    assert session.state is RecognitionState.NEEDS_CONFIRMATION
    corrected = session.confirm_candidate(value=5, monotonic_time=6)
    assert corrected.value == 5
    assert corrected.source is ConfirmationSource.CORRECTED


def test_pipeline_connects_frame_motion_detection_and_confirmation() -> None:
    detection = DieDetection(
        value=3,
        box=np.zeros((4, 2), dtype=np.int32),
        center=(5, 5),
        confidence=0.99,
        pip_centers=((5, 5),),
    )

    class FixedDetector:
        def detect(
            self, image: np.ndarray
        ) -> tuple[list[DieDetection], np.ndarray, int]:
            return [detection], np.zeros(image.shape[:2], dtype=np.uint8), 0

    pipeline = DiceRecognitionPipeline(detector=FixedDetector())
    dark = np.zeros((10, 10, 3), dtype=np.uint8)
    light = np.full((10, 10, 3), 255, dtype=np.uint8)
    assert pipeline.process(dark, monotonic_time=0).snapshot.state is (
        RecognitionState.WAITING
    )
    assert pipeline.process(light, monotonic_time=1).snapshot.state is (
        RecognitionState.MOVING
    )
    for timestamp in (2, 3, 4):
        pipeline.process(light, monotonic_time=timestamp)
    for timestamp in (5, 6, 7):
        pipeline.process(light, monotonic_time=timestamp)
    result = pipeline.process(light, monotonic_time=8)
    assert result.snapshot.state is RecognitionState.CONFIRMED
    assert result.snapshot.confirmation is not None
    assert result.snapshot.confirmation.value == 3


def test_manual_input_validates_value_and_confirmation_id() -> None:
    session = DiceRecognitionSession()
    with pytest.raises(ValueError, match="1〜6"):
        session.enter_manual(7, monotonic_time=1)
    confirmation = session.enter_manual(3, monotonic_time=2)
    assert confirmation.source is ConfirmationSource.MANUAL
    with pytest.raises(ValueError, match="一致"):
        session.acknowledge_committed(confirmation.id + "-wrong")
    session.acknowledge_committed(confirmation.id)


def test_confirmation_id_contains_session_uuid_and_sequence() -> None:
    first = DiceRecognitionSession().enter_manual(2, monotonic_time=1)
    second = DiceRecognitionSession().enter_manual(2, monotonic_time=1)
    assert first.id == f"{first.session_id}:{first.sequence}"
    assert first.sequence == 1
    assert first.session_id != second.session_id
    assert first.id != second.id


def _draw_die(
    canvas: np.ndarray,
    center: tuple[int, int],
    size: int,
    value: int,
    angle: int,
) -> None:
    die = np.zeros((size + 30, size + 30, 3), dtype=np.uint8)
    cv2.rectangle(die, (15, 15), (15 + size, 15 + size), (255, 255, 255), -1)
    radius = max(7, size // 13)
    spacing = size * 0.28
    center_coordinate = 15 + size / 2
    for pip_x, pip_y in PIPS[value]:
        pip_center = (
            round(center_coordinate + pip_x * spacing),
            round(center_coordinate + pip_y * spacing),
        )
        cv2.circle(die, pip_center, radius, (0, 0, 0), -1)
    matrix = cv2.getRotationMatrix2D(
        (die.shape[1] / 2, die.shape[0] / 2), angle, 1
    )
    rotated = cv2.warpAffine(die, matrix, (die.shape[1], die.shape[0]))
    x = center[0] - rotated.shape[1] // 2
    y = center[1] - rotated.shape[0] // 2
    region = canvas[y : y + rotated.shape[0], x : x + rotated.shape[1]]
    mask = cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY) > 0
    region[mask] = rotated[mask]
