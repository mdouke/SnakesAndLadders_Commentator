from pathlib import Path

import cv2
import numpy as np
import pytest
from commentator.services.vision.detector import DieDetection
from commentator.services.vision.evaluation import (
    DecisionType,
    EvaluationDecision,
    EvaluationLabels,
    GroundTruthRoll,
    SavedVideoEvaluator,
    VideoEvaluation,
    load_recognition_config,
)
from commentator.services.vision.recognition import (
    ConfidenceBreakdown,
    RecognitionConfig,
)


def confidence(total: float = 0.9) -> ConfidenceBreakdown:
    return ConfidenceBreakdown(0.9, 1.0, 1.0, 1.0, total)


def test_metrics_include_correct_false_extra_and_latency() -> None:
    labels = EvaluationLabels((
        GroundTruthRoll("roll-1", 4, 1.0, 2.0),
        GroundTruthRoll("roll-2", 2, 3.0, 4.0),
    ))
    decisions = (
        EvaluationDecision(DecisionType.AUTOMATIC, 4, 1.4, confidence()),
        EvaluationDecision(DecisionType.AUTOMATIC, 5, 3.5, confidence()),
        EvaluationDecision(DecisionType.NEEDS_CONFIRMATION, 2, 3.6, confidence(0.7)),
        EvaluationDecision(DecisionType.AUTOMATIC, 1, 5.0, confidence()),
    )
    report = VideoEvaluation(Path("study.avi"), labels, decisions).report()
    summary = report["summary"]
    assert summary["recognitionRate"] == 0.5
    assert summary["eventualRecognitionRate"] == 1.0
    assert summary["automaticRecognitionRate"] == 0.5
    assert summary["falseConfirmationRate"] == pytest.approx(2 / 3)
    assert summary["reviewRequiredRolls"] == 1
    assert summary["extraDecisionCount"] == 1
    assert summary["confirmationTimeSeconds"]["mean"] == pytest.approx(0.4)
    assert report["rolls"][1]["firstDecision"] == report["decisions"][1]
    assert report["rolls"][1]["firstDecision"]["correct"] is False
    assert report["decisions"][2]["confidence"] == {
        "detector": 0.9,
        "valueStability": 1.0,
        "stillness": 1.0,
        "positionStability": 1.0,
        "total": 0.7,
    }


def test_first_wrong_decision_is_not_rescued_by_later_correct_decision() -> None:
    labels = EvaluationLabels((GroundTruthRoll("roll-1", 4, 1.0, 2.0),))
    decisions = (
        EvaluationDecision(DecisionType.AUTOMATIC, 3, 1.2, confidence()),
        EvaluationDecision(DecisionType.AUTOMATIC, 4, 1.4, confidence()),
    )
    report = VideoEvaluation(Path("study.avi"), labels, decisions).report()
    assert report["summary"]["recognitionRate"] == 0.0
    assert report["summary"]["automaticRecognitionRate"] == 0.0
    assert report["summary"]["eventualRecognitionRate"] == 1.0
    assert report["rolls"][0]["firstDecision"]["value"] == 3


def test_discarded_decision_keeps_confidence_without_counting_as_recognized() -> None:
    labels = EvaluationLabels((GroundTruthRoll("roll-1", 4, 1.0, 2.0),))
    decision = EvaluationDecision(
        DecisionType.DISCARDED, 4, 1.2, confidence(0.55)
    )
    report = VideoEvaluation(Path("study.avi"), labels, (decision,)).report()
    assert report["summary"]["recognitionRate"] == 0.0
    assert report["summary"]["discardedCandidateCount"] == 1
    assert report["decisions"][0]["type"] == "discarded"
    assert report["decisions"][0]["confidence"]["total"] == 0.55


def test_labels_reject_overlap_and_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="重複"):
        EvaluationLabels((
            GroundTruthRoll("a", 1, 0.0, 2.0),
            GroundTruthRoll("b", 2, 1.0, 3.0),
        ))
    invalid = tmp_path / "labels.json"
    invalid.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="読み込めません"):
        EvaluationLabels.load(invalid)


def test_recognition_config_file_supports_partial_overrides(tmp_path: Path) -> None:
    path = tmp_path / "recognition.json"
    path.write_text(
        '{"automaticThreshold": 0.9, "stationaryFrames": 4}',
        encoding="utf-8",
    )
    config = load_recognition_config(path)
    assert config.automatic_threshold == 0.9
    assert config.stationary_frames == 4
    assert config.confirmation_threshold == RecognitionConfig().confirmation_threshold


def test_saved_video_runs_without_camera_hardware(tmp_path: Path) -> None:
    video_path = tmp_path / "roll.avi"
    writer = cv2.VideoWriter(
        str(video_path),
        cv2.VideoWriter_fourcc(*"MJPG"),  # type: ignore[attr-defined]
        10.0,
        (64, 64),
    )
    assert writer.isOpened()
    dark = np.zeros((64, 64, 3), dtype=np.uint8)
    light = np.full((64, 64, 3), 255, dtype=np.uint8)
    for image in [dark] * 3 + [light] * 5 + [dark] * 5:
        writer.write(image)
    writer.release()

    detection = DieDetection(
        value=4,
        box=np.zeros((4, 2), dtype=np.int32),
        center=(32, 32),
        confidence=0.99,
        pip_centers=((24, 24), (40, 24), (24, 40), (40, 40)),
    )

    class BrightnessDetector:
        def detect(
            self, frame: np.ndarray
        ) -> tuple[list[DieDetection], np.ndarray, int]:
            detections = [detection] if float(frame.mean()) > 100 else []
            return detections, np.zeros(frame.shape[:2], dtype=np.uint8), 0

    labels = EvaluationLabels((GroundTruthRoll("roll-1", 4, 0.3, 0.8),))
    evaluator = SavedVideoEvaluator(
        detector=BrightnessDetector(),
        config=RecognitionConfig(
            stationary_frames=2,
            consecutive_value_frames=2,
            removal_frames=2,
        ),
    )
    report = evaluator.evaluate(video_path, labels).report()
    assert report["summary"]["recognitionRate"] == 1.0
    assert report["summary"]["falseConfirmationRate"] == 0.0
    assert report["summary"]["confirmationTimeSeconds"]["mean"] == pytest.approx(0.4)
    assert report["videoMetadata"]["fps"] == 10.0
    assert report["videoMetadata"]["totalFrames"] == 13
    assert report["videoMetadata"]["durationSeconds"] == pytest.approx(1.3)
    assert report["recognitionConfig"]["stationaryFrames"] == 2
    assert report["roi"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    assert report["evaluatedAt"].endswith("+00:00")


def test_video_prefers_position_milliseconds_for_variable_frame_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = [np.zeros((8, 8, 3), dtype=np.uint8) for _ in range(3)]

    class VariableRateCapture:
        def __init__(self) -> None:
            self.index = 0

        def isOpened(self) -> bool:
            return True

        def get(self, key: int) -> float:
            if key == cv2.CAP_PROP_FPS:
                return 10.0
            if key == cv2.CAP_PROP_POS_MSEC:
                return [0.0, 100.0, 350.0][max(0, self.index - 1)]
            return 0.0

        def read(self) -> tuple[bool, np.ndarray | None]:
            if self.index >= len(frames):
                return False, None
            result = frames[self.index]
            self.index += 1
            return True, result

        def release(self) -> None:
            pass

    class EmptyDetector:
        def detect(
            self, frame: np.ndarray
        ) -> tuple[list[DieDetection], np.ndarray, int]:
            return [], np.zeros(frame.shape[:2], dtype=np.uint8), 0

    monkeypatch.setattr(cv2, "VideoCapture", lambda _: VariableRateCapture())
    report = SavedVideoEvaluator(detector=EmptyDetector()).evaluate(
        Path("variable.mov"), EvaluationLabels(())
    ).report()
    assert report["videoMetadata"]["totalFrames"] == 3
    assert report["videoMetadata"]["durationSeconds"] == pytest.approx(0.45)
