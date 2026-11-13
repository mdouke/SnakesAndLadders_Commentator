"""Evaluate dice recognition against time-aligned labels for a saved video."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from statistics import mean, median
from typing import Any, cast

import cv2

from .detector import DiceDetector
from .motion import Frame
from .pipeline import DiceRecognitionPipeline, FrameDetector
from .recognition import (
    ConfidenceBreakdown,
    DiceRecognitionSession,
    RecognitionConfig,
    RecognitionState,
)
from .settings import NormalizedRoi, VisionSettingsStore


@dataclass(frozen=True, slots=True)
class GroundTruthRoll:
    id: str
    expected_value: int
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("投擲IDは空にできません")
        if not 1 <= self.expected_value <= 6:
            raise ValueError("正解の出目は1〜6で指定してください")
        if self.start_seconds < 0 or self.end_seconds <= self.start_seconds:
            raise ValueError("投擲区間の時刻が不正です")


class DecisionType(StrEnum):
    AUTOMATIC = "automatic"
    NEEDS_CONFIRMATION = "needs_confirmation"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class EvaluationDecision:
    type: DecisionType
    value: int
    seconds: float
    confidence: ConfidenceBreakdown


@dataclass(frozen=True, slots=True)
class EvaluationLabels:
    rolls: tuple[GroundTruthRoll, ...]

    def __post_init__(self) -> None:
        ids = [roll.id for roll in self.rolls]
        if len(ids) != len(set(ids)):
            raise ValueError("投擲IDが重複しています")
        ordered = sorted(self.rolls, key=lambda roll: roll.start_seconds)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.start_seconds < previous.end_seconds:
                raise ValueError("投擲区間は重複できません")

    @classmethod
    def load(cls, path: Path) -> EvaluationLabels:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            raw_rolls = value["rolls"]
            if not isinstance(raw_rolls, list):
                raise TypeError
            rolls = tuple(
                GroundTruthRoll(
                    id=str(item["id"]),
                    expected_value=int(item["expectedValue"]),
                    start_seconds=float(item["startSeconds"]),
                    end_seconds=float(item["endSeconds"]),
                )
                for item in raw_rolls
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError("正解ラベルを読み込めません") from error
        return cls(rolls)


@dataclass(frozen=True, slots=True)
class VideoEvaluation:
    video_path: Path
    labels: EvaluationLabels
    decisions: tuple[EvaluationDecision, ...]
    config: RecognitionConfig = field(default_factory=RecognitionConfig)
    roi: NormalizedRoi = field(default_factory=NormalizedRoi)
    fps: float = 0.0
    total_frames: int = 0
    duration_seconds: float = 0.0
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def report(self) -> dict[str, Any]:
        matched = [self._match(decision) for decision in self.decisions]
        roll_results: list[dict[str, Any]] = []
        latencies: list[float] = []
        recognized_rolls = 0
        automatic_correct_rolls = 0
        eventual_recognized_rolls = 0
        review_required_rolls = 0
        wrong_candidate_count = 0

        for roll in self.labels.rolls:
            selected = sorted((
                decision
                for decision, matched_roll in zip(self.decisions, matched, strict=True)
                if matched_roll == roll
            ), key=lambda decision: decision.seconds)
            first_decision = selected[0] if selected else None
            recognized = bool(
                first_decision is not None
                and first_decision.type is not DecisionType.DISCARDED
                and first_decision.value == roll.expected_value
            )
            automatic_correct = bool(
                recognized
                and first_decision is not None
                and first_decision.type is DecisionType.AUTOMATIC
            )
            eventual_correct = [
                decision for decision in selected
                if decision.type is not DecisionType.DISCARDED
                and decision.value == roll.expected_value
            ]
            review_required = any(
                decision.type is DecisionType.NEEDS_CONFIRMATION for decision in selected
            )
            latency = (
                first_decision.seconds - roll.start_seconds
                if recognized and first_decision is not None
                else None
            )
            if recognized:
                recognized_rolls += 1
            if automatic_correct:
                automatic_correct_rolls += 1
            if eventual_correct:
                eventual_recognized_rolls += 1
            if review_required:
                review_required_rolls += 1
            wrong_candidate_count += sum(
                decision.value != roll.expected_value for decision in selected
            )
            if latency is not None:
                latencies.append(latency)
            roll_results.append({
                "id": roll.id,
                "expectedValue": roll.expected_value,
                "recognized": recognized,
                "automaticCorrect": automatic_correct,
                "reviewRequired": review_required,
                "confirmationSeconds": latency,
                "eventualCorrect": bool(eventual_correct),
                "firstDecision": (
                    _decision_summary(first_decision, roll)
                    if first_decision is not None else None
                ),
                "decisionCount": len(selected),
            })

        automatic = [
            decision for decision in self.decisions
            if decision.type is DecisionType.AUTOMATIC
        ]
        false_automatic = sum(
            roll is None or decision.value != roll.expected_value
            for decision, roll in zip(self.decisions, matched, strict=True)
            if decision.type is DecisionType.AUTOMATIC
        )
        total_rolls = len(self.labels.rolls)
        extra_decisions = sum(roll is None for roll in matched)
        discarded = sum(
            decision.type is DecisionType.DISCARDED for decision in self.decisions
        )
        return {
            "video": str(self.video_path),
            "evaluatedAt": self.evaluated_at.astimezone(UTC).isoformat(),
            "videoMetadata": {
                "fps": self.fps,
                "totalFrames": self.total_frames,
                "durationSeconds": self.duration_seconds,
            },
            "recognitionConfig": _config_dict(self.config),
            "roi": asdict(self.roi),
            "summary": {
                "totalRolls": total_rolls,
                "recognizedRolls": recognized_rolls,
                "recognitionRate": _ratio(recognized_rolls, total_rolls),
                "eventualRecognizedRolls": eventual_recognized_rolls,
                "eventualRecognitionRate": _ratio(
                    eventual_recognized_rolls, total_rolls
                ),
                "automaticCorrectRolls": automatic_correct_rolls,
                "automaticRecognitionRate": _ratio(
                    automatic_correct_rolls, total_rolls
                ),
                "automaticConfirmations": len(automatic),
                "falseAutomaticConfirmations": false_automatic,
                "falseConfirmationRate": _ratio(false_automatic, len(automatic)),
                "reviewRequiredRolls": review_required_rolls,
                "missedRolls": total_rolls - recognized_rolls,
                "wrongCandidateCount": wrong_candidate_count,
                "extraDecisionCount": extra_decisions,
                "discardedCandidateCount": discarded,
                "confirmationTimeSeconds": _latency_summary(latencies),
            },
            "rolls": roll_results,
            "decisions": [
                _decision_summary(decision, roll)
                for decision, roll in zip(self.decisions, matched, strict=True)
            ],
        }

    def _match(self, decision: EvaluationDecision) -> GroundTruthRoll | None:
        return next(
            (
                roll for roll in self.labels.rolls
                if roll.start_seconds <= decision.seconds < roll.end_seconds
            ),
            None,
        )


class SavedVideoEvaluator:
    def __init__(
        self,
        *,
        detector: FrameDetector | None = None,
        config: RecognitionConfig | None = None,
        roi: NormalizedRoi | None = None,
    ) -> None:
        self.detector = detector
        self.config = config or RecognitionConfig()
        self.roi = roi or NormalizedRoi()

    def evaluate(self, video_path: Path, labels: EvaluationLabels) -> VideoEvaluation:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            capture.release()
            raise ValueError(f"保存映像を開けません: {video_path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if fps <= 0:
            capture.release()
            raise ValueError("保存映像のFPSを取得できません")
        session = DiceRecognitionSession(self.config)
        pipeline = DiceRecognitionPipeline(
            detector=self.detector,
            session=session,
            roi=self.roi,
        )
        decisions: list[EvaluationDecision] = []
        frame_index = 0
        previous_seconds = -1.0
        last_seconds = 0.0
        try:
            while True:
                success, frame = capture.read()
                if not success or frame is None:
                    break
                fallback_seconds = frame_index / fps
                position_milliseconds = float(capture.get(cv2.CAP_PROP_POS_MSEC))
                position_seconds = position_milliseconds / 1000.0
                if (
                    isfinite(position_seconds)
                    and position_seconds >= 0
                    and (frame_index == 0 or position_seconds > previous_seconds)
                ):
                    seconds = position_seconds
                else:
                    seconds = max(fallback_seconds, previous_seconds)
                result = pipeline.process(cast(Frame, frame), monotonic_time=seconds)
                snapshot = result.snapshot
                if (
                    snapshot.state is RecognitionState.CONFIRMED
                    and snapshot.confirmation is not None
                    and snapshot.confidence is not None
                ):
                    decisions.append(EvaluationDecision(
                        DecisionType.AUTOMATIC,
                        snapshot.confirmation.value,
                        seconds,
                        snapshot.confidence,
                    ))
                    session.acknowledge_committed(snapshot.confirmation.id)
                elif (
                    snapshot.state is RecognitionState.NEEDS_CONFIRMATION
                    and snapshot.candidate_value is not None
                    and snapshot.confidence is not None
                ):
                    decisions.append(EvaluationDecision(
                        DecisionType.NEEDS_CONFIRMATION,
                        snapshot.candidate_value,
                        seconds,
                        snapshot.confidence,
                    ))
                    session.reject_candidate()
                elif snapshot.discarded_candidate is not None:
                    decisions.append(EvaluationDecision(
                        DecisionType.DISCARDED,
                        snapshot.discarded_candidate.value,
                        seconds,
                        snapshot.discarded_candidate.confidence,
                    ))
                previous_seconds = seconds
                last_seconds = seconds
                frame_index += 1
        finally:
            capture.release()
        duration_seconds = last_seconds + (1 / fps if frame_index else 0.0)
        return VideoEvaluation(
            video_path=video_path,
            labels=labels,
            decisions=tuple(decisions),
            config=self.config,
            roi=self.roi,
            fps=fps,
            total_frames=frame_index,
            duration_seconds=duration_seconds,
        )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _confidence_dict(value: ConfidenceBreakdown) -> dict[str, float]:
    raw = asdict(value)
    return {
        "detector": raw["detector"],
        "valueStability": raw["value_stability"],
        "stillness": raw["stillness"],
        "positionStability": raw["position_stability"],
        "total": raw["total"],
    }


def _decision_summary(
    decision: EvaluationDecision,
    roll: GroundTruthRoll | None,
) -> dict[str, Any]:
    return {
        "type": decision.type.value,
        "value": decision.value,
        "seconds": decision.seconds,
        "rollId": roll.id if roll is not None else None,
        "correct": decision.value == roll.expected_value if roll is not None else False,
        "confidence": _confidence_dict(decision.confidence),
    }


def _config_dict(value: RecognitionConfig) -> dict[str, float | int]:
    raw = asdict(value)
    return {
        "movementThreshold": raw["movement_threshold"],
        "stationaryThreshold": raw["stationary_threshold"],
        "stationaryFrames": raw["stationary_frames"],
        "consecutiveValueFrames": raw["consecutive_value_frames"],
        "candidateWindowFrames": raw["candidate_window_frames"],
        "removalFrames": raw["removal_frames"],
        "automaticThreshold": raw["automatic_threshold"],
        "confirmationThreshold": raw["confirmation_threshold"],
        "recognitionTimeoutSeconds": raw["recognition_timeout_seconds"],
    }


def load_recognition_config(path: Path) -> RecognitionConfig:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError
        defaults = _config_dict(RecognitionConfig())
        if set(value) - set(defaults):
            raise ValueError
        selected = {**defaults, **value}
        return RecognitionConfig(
            movement_threshold=float(selected["movementThreshold"]),
            stationary_threshold=float(selected["stationaryThreshold"]),
            stationary_frames=int(selected["stationaryFrames"]),
            consecutive_value_frames=int(selected["consecutiveValueFrames"]),
            candidate_window_frames=int(selected["candidateWindowFrames"]),
            removal_frames=int(selected["removalFrames"]),
            automatic_threshold=float(selected["automaticThreshold"]),
            confirmation_threshold=float(selected["confirmationThreshold"]),
            recognition_timeout_seconds=float(
                selected["recognitionTimeoutSeconds"]
            ),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("認識設定を読み込めません") from error


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": mean(values) if values else None,
        "median": median(values) if values else None,
        "p95": _percentile(values, 0.95) if values else None,
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def main() -> None:
    parser = argparse.ArgumentParser(description="保存映像のサイコロ認識を評価します")
    parser.add_argument("video", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    labels = EvaluationLabels.load(arguments.labels)
    vision_settings = (
        VisionSettingsStore(arguments.settings).load()
        if arguments.settings is not None else None
    )
    roi = vision_settings.roi if vision_settings is not None else None
    detector = (
        DiceDetector(white_threshold=vision_settings.white_threshold)
        if vision_settings is not None else None
    )
    config = (
        load_recognition_config(arguments.config)
        if arguments.config is not None else None
    )
    report = SavedVideoEvaluator(
        detector=detector, roi=roi, config=config
    ).evaluate(
        arguments.video, labels
    ).report()
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is None:
        print(output, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
