from pathlib import Path

import cv2
import numpy as np
import pytest
from commentator.services.vision.detector import (
    DiceDetector,
    DieDetection,
    _order_points,
    pip_layout_error,
)
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
    pip_size_consistency: float = 1.0,
    single_pip_center_offset: float = 0.0,
    pip_merge_suspected: bool = False,
    pip_recounted: bool = False,
    pip_layout_error: float = 0.0,
) -> FrameObservation:
    detections = (
        (
            DieObservation(
                value,
                confidence,
                center,
                pip_size_consistency,
                single_pip_center_offset,
                pip_merge_suspected,
                pip_recounted,
                pip_layout_error,
            ),
        )
        if value is not None else ()
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


@pytest.mark.parametrize(
    "angle",
    sorted(set(range(91)) | {round(value / 10, 1) for value in range(440, 461)}),
)
def test_rotated_square_warp_always_uses_four_distinct_corners(
    angle: float,
) -> None:
    gray = np.zeros((300, 300), dtype=np.uint8)
    points = cv2.boxPoints(((150.0, 150.0), (90.0, 90.0), angle))
    cv2.fillConvexPoly(gray, np.rint(points).astype(np.int32), 255)

    ordered = _order_points(points)
    warped, _ = DiceDetector._warp_die(gray, ordered)

    assert np.unique(ordered, axis=0).shape[0] == 4
    assert float(warped.mean()) > 245


def test_detector_counts_three_pips_on_exactly_45_degree_die() -> None:
    canvas = np.zeros((400, 400, 3), dtype=np.uint8)
    _draw_die(canvas, (200, 200), 120, 3, 45)

    detected, _, rejected_count = DiceDetector(
        white_threshold=230,
        pip_threshold=235,
    ).detect(canvas)

    assert [die.value for die in detected] == [3]
    assert rejected_count == 0


def test_detector_counts_corner_pips_close_to_warped_face_edge() -> None:
    warped = np.full((180, 180), 255, dtype=np.uint8)
    for point in ((18, 18), (90, 90), (162, 162)):
        cv2.circle(warped, point, 9, 0, -1)

    pips = DiceDetector()._find_pips(warped)

    assert len(pips) == 3


def test_detector_counts_pip_notched_by_specular_highlight() -> None:
    # A highlight on a recessed pip cuts a bright notch into its edge. The raw
    # contour becomes a crescent, but it is still a pip.
    warped = np.full((180, 180), 255, dtype=np.uint8)
    for point in ((50, 50), (90, 90), (130, 130)):
        cv2.circle(warped, point, 13, 0, -1)
    cv2.circle(warped, (136, 124), 12, 255, -1)

    pips = DiceDetector()._find_pips(warped)

    assert len(pips) == 3

    analysis = DiceDetector()._analyze_pips(warped, collect_debug=True)
    assert sum(item.accepted for item in analysis.contours) == 3
    assert all(item.rejection_reason is None for item in analysis.contours)
    assert analysis.threshold_value > 0
    assert analysis.threshold_is_automatic


def test_detector_ignores_thin_dark_arc() -> None:
    warped = np.full((180, 180), 255, dtype=np.uint8)
    cv2.circle(warped, (60, 60), 14, 0, -1)
    cv2.ellipse(warped, (120, 120), (16, 16), 0, 200, 340, 0, 3)

    pips = DiceDetector()._find_pips(warped)

    assert len(pips) == 1

    analysis = DiceDetector()._analyze_pips(warped, collect_debug=True)
    accepted = [item for item in analysis.contours if item.accepted]
    rejected = [item for item in analysis.contours if not item.accepted]
    assert len(accepted) == 1
    assert rejected
    assert all(item.rejection_reason in {
        "area_small",
        "area_large",
        "circularity",
        "solidity",
        "aspect",
    } for item in rejected)
    assert all(item.circularity >= 0 and item.solidity >= 0 for item in rejected)


def test_pip_debug_flag_does_not_change_detection_result() -> None:
    image = cv2.imread(str(FIXTURES / "demo_dice.png"))
    assert image is not None
    normal = DiceDetector(debug_pips=False)
    debug = DiceDetector(debug_pips=True)

    normal_detections, normal_mask, normal_rejected = normal.detect(image)
    debug_detections, debug_mask, debug_rejected = debug.detect(image)

    assert [item.value for item in normal_detections] == [
        item.value for item in debug_detections
    ]
    assert normal_rejected == debug_rejected
    assert np.array_equal(normal_mask, debug_mask)
    assert normal.last_pip_debug == ()
    assert len(debug.last_pip_debug) == len(debug_detections) + debug_rejected
    assert [item.candidate_index for item in debug.last_pip_debug] == list(
        range(1, len(debug.last_pip_debug) + 1)
    )
    assert all(
        sum(contour.accepted for contour in item.contours) == item.counted_pips
        for item in debug.last_pip_debug
    )


def test_fixed_pip_threshold_controls_dark_pip_extraction() -> None:
    warped = np.full((180, 180), 230, dtype=np.uint8)
    for point in ((50, 50), (90, 90), (130, 130)):
        cv2.circle(warped, point, 11, 80, -1)

    too_dark = DiceDetector(pip_threshold=50)._analyze_pips(
        warped, collect_debug=True
    )
    selected = DiceDetector(pip_threshold=120)._analyze_pips(
        warped, collect_debug=True
    )

    assert too_dark.threshold_value == 50
    assert not too_dark.threshold_is_automatic
    assert len(too_dark.centers) == 0
    assert selected.threshold_value == 120
    assert not selected.threshold_is_automatic
    assert len(selected.centers) == 3


def test_pip_size_consistency_detects_one_abnormally_small_pip() -> None:
    warped = np.full((180, 180), 255, dtype=np.uint8)
    cv2.circle(warped, (50, 50), 7, 0, -1)
    cv2.circle(warped, (90, 90), 11, 0, -1)
    cv2.circle(warped, (130, 130), 11, 0, -1)

    analysis = DiceDetector(pip_threshold=235)._analyze_pips(
        warped, collect_debug=True
    )

    assert len(analysis.centers) == 3
    assert analysis.pip_size_consistency < 0.5


@pytest.mark.parametrize("value", range(2, 7))
def test_normal_multi_pip_faces_have_consistent_pip_sizes(value: int) -> None:
    warped = np.full((180, 180), 255, dtype=np.uint8)
    for pip_x, pip_y in PIPS[value]:
        cv2.circle(
            warped,
            (round(90 + pip_x * 42), round(90 + pip_y * 42)),
            9,
            0,
            -1,
        )

    analysis = DiceDetector(pip_threshold=235)._analyze_pips(
        warped, collect_debug=False
    )

    assert len(analysis.centers) == value
    assert analysis.pip_size_consistency >= 0.95
    assert analysis.single_pip_center_offset == 0.0


def test_single_pip_center_offset_distinguishes_center_from_corner() -> None:
    centered = np.full((180, 180), 255, dtype=np.uint8)
    cv2.circle(centered, (90, 90), 10, 0, -1)
    corner = np.full((180, 180), 255, dtype=np.uint8)
    cv2.circle(corner, (50, 50), 10, 0, -1)
    detector = DiceDetector(pip_threshold=235)

    centered_analysis = detector._analyze_pips(centered, collect_debug=False)
    corner_analysis = detector._analyze_pips(corner, collect_debug=False)

    assert centered_analysis.single_pip_center_offset == pytest.approx(0.0)
    assert corner_analysis.single_pip_center_offset > 0.22


def _six_face_with_fused_column(*, bridge: int) -> np.ndarray:
    """A die face whose right pip column is joined by a light-gray bridge.

    The bridge is darker than a high fixed pip threshold but lighter than a
    lower one, like the halo between closely spaced pips on a tilted die.
    """
    warped = np.full((180, 180), 255, dtype=np.uint8)
    for pip_x, pip_y in PIPS[6]:
        cv2.circle(
            warped, (round(90 + pip_x * 42), round(90 + pip_y * 42)), 10, 0, -1
        )
    cv2.rectangle(warped, (126, 48), (138, 132), bridge, -1)
    for pip_y in (-1, 0, 1):
        cv2.circle(warped, (132, round(90 + pip_y * 42)), 10, 0, -1)
    return warped


def test_fused_pips_inside_face_are_flagged() -> None:
    warped = _six_face_with_fused_column(bridge=228)

    analysis = DiceDetector(pip_threshold=235)._analyze_pips(
        warped, collect_debug=False
    )

    assert analysis.merged_pip_blob
    assert len(analysis.centers) == 3


def test_fused_pair_below_large_area_limit_is_flagged() -> None:
    # Two touching pips form a blob under the area_large limit that is
    # rejected for its shape; it must still count as fused pips.
    warped = np.full((180, 180), 255, dtype=np.uint8)
    cv2.circle(warped, (79, 90), 14, 0, -1)
    cv2.circle(warped, (101, 90), 14, 0, -1)

    analysis = DiceDetector(pip_threshold=235)._analyze_pips(
        warped, collect_debug=True
    )

    rejected = [item for item in analysis.contours if not item.accepted]
    assert rejected and rejected[0].rejection_reason != "area_large"
    assert analysis.merged_pip_blob


def test_dark_region_touching_face_border_is_not_fused_pips() -> None:
    warped = np.full((180, 180), 255, dtype=np.uint8)
    for pip_x, pip_y in PIPS[3]:
        cv2.circle(
            warped, (round(90 + pip_x * 42), round(90 + pip_y * 42)), 10, 0, -1
        )
    # A visible side face or shadow enters from the border of the warped face.
    cv2.rectangle(warped, (0, 150), (179, 179), 0, -1)

    analysis = DiceDetector(pip_threshold=235)._analyze_pips(
        warped, collect_debug=False
    )

    assert not analysis.merged_pip_blob


@pytest.mark.parametrize("value", range(1, 7))
def test_normal_faces_are_not_flagged_as_fused(value: int) -> None:
    warped = np.full((180, 180), 255, dtype=np.uint8)
    for pip_x, pip_y in PIPS[value]:
        cv2.circle(
            warped, (round(90 + pip_x * 42), round(90 + pip_y * 42)), 10, 0, -1
        )

    analysis = DiceDetector(pip_threshold=235)._analyze_pips(
        warped, collect_debug=False
    )

    assert not analysis.merged_pip_blob


def test_detector_recounts_fused_pips_with_lower_threshold() -> None:
    canvas = np.zeros((300, 300, 3), dtype=np.uint8)
    face = cv2.resize(_six_face_with_fused_column(bridge=228), (120, 120))
    canvas[90:210, 90:210] = cv2.cvtColor(face, cv2.COLOR_GRAY2BGR)

    detector = DiceDetector(white_threshold=200, pip_threshold=235, debug_pips=True)
    detected, _, _ = detector.detect(canvas)

    assert [item.value for item in detected] == [6]
    assert detected[0].pip_merge_suspected
    assert detected[0].pip_recounted
    assert detector.last_pip_debug[0].pip_recounted
    assert detector.last_pip_debug[0].threshold_value < 235


def test_detector_recounts_strongly_fused_pips_down_the_ladder() -> None:
    canvas = np.zeros((300, 300, 3), dtype=np.uint8)
    # A bridge at 200 survives the first steps (225, 220, 215) and splits only
    # once the threshold drops below it.
    face = cv2.resize(_six_face_with_fused_column(bridge=200), (120, 120))
    canvas[90:210, 90:210] = cv2.cvtColor(face, cv2.COLOR_GRAY2BGR)

    detector = DiceDetector(white_threshold=150, pip_threshold=235, debug_pips=True)
    detected, _, _ = detector.detect(canvas)

    assert [item.value for item in detected] == [6]
    assert detected[0].pip_recounted
    assert detector.last_pip_debug[0].threshold_value < 200


def test_detector_keeps_merge_flag_when_recount_fails() -> None:
    canvas = np.zeros((300, 300, 3), dtype=np.uint8)
    # A bridge as dark as the pips cannot be split by lowering the threshold.
    face = cv2.resize(_six_face_with_fused_column(bridge=0), (120, 120))
    canvas[90:210, 90:210] = cv2.cvtColor(face, cv2.COLOR_GRAY2BGR)

    detected, _, _ = DiceDetector(white_threshold=200, pip_threshold=235).detect(canvas)

    assert [item.value for item in detected] == [3]
    assert detected[0].pip_merge_suspected
    assert not detected[0].pip_recounted


def _layout_points(
    value: int,
    *,
    spacing: tuple[float, float] = (42.0, 42.0),
    offset: tuple[float, float] = (90.0, 90.0),
    rows: bool = False,
) -> tuple[tuple[float, float], ...]:
    points = PIPS[value]
    if rows:
        points = [(y, x) for x, y in points]
    return tuple(
        (offset[0] + x * spacing[0], offset[1] + y * spacing[1]) for x, y in points
    )


@pytest.mark.parametrize("value", range(1, 7))
def test_canonical_layouts_have_no_layout_error(value: int) -> None:
    assert pip_layout_error(_layout_points(value)) == pytest.approx(0.0, abs=1e-9)


def test_rotated_six_has_no_layout_error() -> None:
    assert pip_layout_error(_layout_points(6, rows=True)) == pytest.approx(
        0.0, abs=1e-9
    )


@pytest.mark.parametrize("value", (4, 5, 6))
def test_oblique_foreshortened_face_has_no_layout_error(value: int) -> None:
    # A die seen at an angle: the top face sits off-centre in the warped box and
    # is compressed along one axis.
    points = _layout_points(value, spacing=(32.0, 14.0), offset=(74.0, 62.0))
    assert pip_layout_error(points) == pytest.approx(0.0, abs=1e-9)


def test_side_face_pips_counted_as_a_six_give_layout_error() -> None:
    # Warped pip centres from roll-031 (recording 173133): a 4 on top plus two
    # pips of the near side face below it were read as 6. The side-face row is
    # unevenly spaced and shifted inwards, so no 6 layout fits.
    centers = (
        (117.0, 152.0), (48.0, 152.0), (129.0, 86.0),
        (58.0, 85.0), (127.0, 33.0), (56.0, 32.0),
    )
    assert pip_layout_error(centers) > 0.15


def test_five_missing_a_corner_pip_gives_layout_error() -> None:
    five = list(_layout_points(5))
    del five[0]
    assert pip_layout_error(tuple(five)) > 0.15


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


def test_rejected_outline_keeps_confirmed_die_in_removal_wait() -> None:
    session = ready_session()
    confirmed = session.observe(frame(5, 0.0, value=4)).confirmation
    assert confirmed is not None
    session.acknowledge_committed(confirmed.id)

    for timestamp in (6, 7, 8, 9):
        result = session.observe(frame(timestamp, 0.0, rejected_count=1))
        assert result.state is RecognitionState.REMOVAL_WAIT

    session.observe(frame(10, 0.0))
    removed = session.observe(frame(11, 0.0))
    assert removed.state is RecognitionState.WAITING


def test_static_die_at_confirmed_position_cannot_start_duplicate_cycle() -> None:
    session = ready_session()
    confirmed = session.observe(frame(5, 0.0, value=4)).confirmation
    assert confirmed is not None
    session.acknowledge_committed(confirmed.id)
    session.observe(frame(6, 0.0))
    assert session.observe(frame(7, 0.0)).state is RecognitionState.WAITING

    for timestamp in range(8, 20):
        result = session.observe(frame(float(timestamp), 0.0, value=4))
        assert result.state is RecognitionState.WAITING
        assert result.confirmation is None

    restarted = session.observe(frame(20.0, 0.2, value=4))
    assert restarted.state is RecognitionState.MOVING


def test_stationary_die_at_new_position_can_start_missed_motion_cycle() -> None:
    session = ready_session()
    confirmed = session.observe(frame(5, 0.0, value=4)).confirmation
    assert confirmed is not None
    session.acknowledge_committed(confirmed.id)
    session.observe(frame(6, 0.0))
    assert session.observe(frame(7, 0.0)).state is RecognitionState.WAITING

    restarted = session.observe(
        frame(8, 0.0, value=2, center=(80.0, 80.0))
    )
    assert restarted.state is RecognitionState.MOVING


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


def test_uneven_pip_sizes_require_confirmation_even_with_high_confidence() -> None:
    session = ready_session()

    result = session.observe(
        frame(5, 0.0, value=4, pip_size_consistency=0.28)
    )

    assert result.state is RecognitionState.NEEDS_CONFIRMATION
    assert result.confidence is not None
    assert result.confidence.total >= 0.85
    assert result.confidence.pip_size_consistency == pytest.approx(0.28)


def test_off_center_single_pip_requires_confirmation() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        consecutive_value_frames=3,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.0))
    session.observe(frame(2, 0.0))
    for timestamp in (3, 4):
        session.observe(
            frame(timestamp, 0.0, value=1, single_pip_center_offset=0.27)
        )

    result = session.observe(
        frame(5, 0.0, value=1, single_pip_center_offset=0.27)
    )

    assert result.state is RecognitionState.NEEDS_CONFIRMATION
    assert result.confidence is not None
    assert result.confidence.total >= 0.85
    assert result.confidence.single_pip_center_offset == pytest.approx(0.27)


def test_fused_pip_face_requires_confirmation_even_with_high_confidence() -> None:
    session = ready_session()

    result = session.observe(
        frame(5, 0.0, value=4, pip_merge_suspected=True)
    )

    assert result.state is RecognitionState.NEEDS_CONFIRMATION
    assert result.confidence is not None
    assert result.confidence.total >= 0.85
    assert result.confidence.pip_merge_suspected


def test_successfully_recounted_fused_face_confirms_automatically() -> None:
    session = ready_session()

    result = session.observe(
        frame(5, 0.0, value=4, pip_merge_suspected=True, pip_recounted=True)
    )

    assert result.state is RecognitionState.CONFIRMED
    assert result.confidence is not None
    assert result.confidence.pip_merge_suspected
    assert result.confidence.pip_recounted


def test_single_frame_layout_spike_does_not_block_confirmation() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        consecutive_value_frames=3,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.0))
    session.observe(frame(2, 0.0))
    session.observe(frame(3, 0.0, value=5, pip_layout_error=0.10))
    session.observe(frame(4, 0.0, value=5, pip_layout_error=0.10))

    result = session.observe(frame(5, 0.0, value=5, pip_layout_error=0.16))

    assert result.state is RecognitionState.CONFIRMED
    assert result.confidence is not None
    assert result.confidence.pip_layout_error == pytest.approx(0.10)


def test_persistent_layout_error_requires_confirmation() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        consecutive_value_frames=3,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.0))
    session.observe(frame(2, 0.0))
    session.observe(frame(3, 0.0, value=6, pip_layout_error=0.17))
    session.observe(frame(4, 0.0, value=6, pip_layout_error=0.10))

    result = session.observe(frame(5, 0.0, value=6, pip_layout_error=0.18))

    assert result.state is RecognitionState.NEEDS_CONFIRMATION
    assert result.confidence is not None
    assert result.confidence.pip_layout_error == pytest.approx(0.17)


def test_oblique_die_with_low_shape_confidence_requires_confirmation() -> None:
    # roll-021 (180356): a 4 with its side face visible was read as 6 with a
    # total of 0.84 but a shape confidence of 0.77.
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        consecutive_value_frames=3,
        automatic_threshold=0.70,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.0))
    session.observe(frame(2, 0.0))
    for timestamp in (3, 4, 5):
        result = session.observe(frame(timestamp, 0.0, value=6, confidence=0.77))

    assert result.state is RecognitionState.NEEDS_CONFIRMATION
    assert result.confidence is not None
    assert result.confidence.total >= 0.70
    assert result.confidence.detector == pytest.approx(0.77)


def test_die_just_above_shape_confidence_limit_still_confirms() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        consecutive_value_frames=3,
        automatic_threshold=0.70,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.0))
    session.observe(frame(2, 0.0))
    for timestamp in (3, 4, 5):
        result = session.observe(frame(timestamp, 0.0, value=6, confidence=0.79))

    assert result.state is RecognitionState.CONFIRMED


def test_pip_layout_error_within_limit_still_confirms() -> None:
    session = ready_session()

    result = session.observe(frame(5, 0.0, value=4, pip_layout_error=0.12))

    assert result.state is RecognitionState.CONFIRMED


def test_centered_single_pip_still_confirms_automatically() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        consecutive_value_frames=3,
    ))
    session.observe(frame(0, 0.2))
    session.observe(frame(1, 0.0))
    session.observe(frame(2, 0.0))
    for timestamp in (3, 4):
        session.observe(
            frame(timestamp, 0.0, value=1, single_pip_center_offset=0.10)
        )

    result = session.observe(
        frame(5, 0.0, value=1, single_pip_center_offset=0.10)
    )

    assert result.state is RecognitionState.CONFIRMED


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


def test_default_stationary_window_handles_low_frame_rate_camera() -> None:
    session = DiceRecognitionSession()
    session.observe(frame(0, 0.2))
    for timestamp in range(1, 10):
        result = session.observe(frame(timestamp / 10, 0.0))
        assert result.state is RecognitionState.MOVING
    assert session.observe(frame(1.0, 0.0)).state is RecognitionState.STATIONARY
    session.observe(frame(1.1, 0.0, value=3))
    session.observe(frame(1.2, 0.0, value=3))
    confirmed = session.observe(frame(1.3, 0.0, value=3))
    assert confirmed.state is RecognitionState.CONFIRMED
    assert confirmed.confirmation is not None
    assert confirmed.confirmation.value == 3


def test_visible_die_starts_cycle_when_initial_motion_was_missed() -> None:
    session = DiceRecognitionSession(RecognitionConfig(
        stationary_frames=2,
        consecutive_value_frames=3,
        removal_frames=2,
    ))

    started = session.observe(frame(0, 0.006, value=3))
    assert started.state is RecognitionState.MOVING
    session.observe(frame(1, 0.0, value=3))
    assert session.observe(frame(2, 0.0, value=3)).state is RecognitionState.STATIONARY
    session.observe(frame(3, 0.0, value=3))
    session.observe(frame(4, 0.0, value=3))
    confirmed = session.observe(frame(5, 0.0, value=3))

    assert confirmed.state is RecognitionState.CONFIRMED
    assert confirmed.confirmation is not None
    assert confirmed.confirmation.value == 3


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
            detections = [detection] if image.any() else []
            return detections, np.zeros(image.shape[:2], dtype=np.uint8), 0

    pipeline = DiceRecognitionPipeline(
        detector=FixedDetector(),
        session=DiceRecognitionSession(RecognitionConfig(stationary_frames=3)),
    )
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
