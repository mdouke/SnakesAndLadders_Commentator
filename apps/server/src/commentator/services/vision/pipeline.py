"""Connect frame-level detection and motion analysis to the temporal state machine."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Protocol

from .detector import DiceDetector, DieDetection
from .motion import Frame, frame_motion_score
from .recognition import (
    DiceRecognitionSession,
    DieObservation,
    FrameObservation,
    RecognitionSnapshot,
)
from .settings import NormalizedRoi


class FrameDetector(Protocol):
    def detect(self, frame: Frame) -> tuple[list[DieDetection], Frame]: ...


@dataclass(frozen=True, slots=True)
class RecognitionFrameResult:
    snapshot: RecognitionSnapshot
    motion_score: float
    detections: tuple[DieDetection, ...]


class DiceRecognitionPipeline:
    def __init__(
        self,
        *,
        detector: FrameDetector | None = None,
        session: DiceRecognitionSession | None = None,
        roi: NormalizedRoi | None = None,
    ) -> None:
        self.detector = detector or DiceDetector()
        self.session = session or DiceRecognitionSession()
        self.roi = roi or NormalizedRoi()
        self._previous_frame: Frame | None = None

    def process(self, frame: Frame, *, monotonic_time: float) -> RecognitionFrameResult:
        cropped = self.roi.crop(frame)
        motion_score = (
            frame_motion_score(self._previous_frame, cropped)
            if self._previous_frame is not None
            else 0.0
        )
        detections, _ = self.detector.detect(cropped)
        diagonal = hypot(cropped.shape[1], cropped.shape[0])
        observations = tuple(
            DieObservation(
                value=detection.value,
                detector_confidence=detection.confidence,
                center=(float(detection.center[0]), float(detection.center[1])),
            )
            for detection in detections
        )
        snapshot = self.session.observe(FrameObservation(
            monotonic_time=monotonic_time,
            motion_score=motion_score,
            detections=observations,
            roi_diagonal=diagonal,
        ))
        self._previous_frame = cropped.copy()
        return RecognitionFrameResult(snapshot, motion_score, tuple(detections))

    def camera_disconnected(self, *, monotonic_time: float) -> RecognitionSnapshot:
        self._previous_frame = None
        return self.session.observe(FrameObservation(
            monotonic_time=monotonic_time,
            motion_score=0.0,
            camera_connected=False,
        ))

    def reset_frames(self) -> None:
        self._previous_frame = None
