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
    assert summary["recognitionRate"] == 1.0
    assert summary["automaticRecognitionRate"] == 0.5
    assert summary["falseConfirmationRate"] == pytest.approx(2 / 3)
    assert summary["reviewRequiredRolls"] == 1
    assert summary["extraDecisionCount"] == 1
    assert summary["confirmationTimeSeconds"]["mean"] == pytest.approx(0.5)
    assert report["decisions"][2]["confidence"] == {
        "detector": 0.9,
        "valueStability": 1.0,
        "stillness": 1.0,
        "positionStability": 1.0,
        "total": 0.7,
    }


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
        def detect(self, frame: np.ndarray) -> tuple[list[DieDetection], np.ndarray]:
            detections = [detection] if float(frame.mean()) > 100 else []
            return detections, np.zeros(frame.shape[:2], dtype=np.uint8)

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
