"""Connect frame-level detection and motion analysis to the temporal state machine."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from time import monotonic
from typing import Protocol

import cv2
import numpy as np

from .detector import (
    DiceDetector,
    DieDetection,
    PipDetectionDebug,
    RejectedDieDetection,
)
from .motion import Frame, Roi, frame_motion_score
from .recognition import (
    DiceRecognitionSession,
    DieObservation,
    FrameObservation,
    RecognitionSnapshot,
    RecognitionState,
)
from .settings import NormalizedRoi
from .tray import TrayTracker

# Look for the tray every few frames between throws (about 3 times a second).
TRAY_UPDATE_INTERVAL_FRAMES = 10
TRAY_MASK_MARGIN = 0.015  # of the tray's size


class FrameDetector(Protocol):
    def detect(self, frame: Frame) -> tuple[list[DieDetection], Frame, int]: ...


@dataclass(frozen=True, slots=True)
class RecognitionFrameResult:
    snapshot: RecognitionSnapshot
    motion_score: float
    detections: tuple[DieDetection, ...]
    rejected_count: int
    white_mask: Frame
    rejected_detections: tuple[RejectedDieDetection, ...] = ()
    pip_debug: tuple[PipDetectionDebug, ...] = ()


class DiceRecognitionPipeline:
    def __init__(
        self,
        *,
        detector: FrameDetector | None = None,
        session: DiceRecognitionSession | None = None,
        roi: NormalizedRoi | None = None,
        tray_detection: bool = False,
    ) -> None:
        self.detector = detector or DiceDetector()
        self.session = session or DiceRecognitionSession()
        self.roi = roi or NormalizedRoi()
        self._previous_frame: Frame | None = None
        # With tray detection the ROI is the search area and the tray outline
        # decides what is recognized and sent to the vision model.
        self.tray_detection = tray_detection
        self.tray = TrayTracker()
        self._frame_index = 0
        self._last_motion = 0.0
        self._mask_cache: tuple[tuple[object, ...], np.ndarray] | None = None

    def set_tray_detection(self, enabled: bool) -> None:
        if enabled != self.tray_detection:
            self.tray_detection = enabled
            self.tray.reset()
            self._previous_frame = None

    def region(self, frame: Frame) -> tuple[Roi, np.ndarray | None]:
        """The crop rectangle in pixels and, with a detected tray, its mask."""
        height, width = frame.shape[:2]
        shape = self.tray.shape if self.tray_detection else None
        if shape is None:
            return self.roi.to_pixels(frame), None
        bounds = shape.bounds(width, height)
        key = (shape, width, height)
        if self._mask_cache is None or self._mask_cache[0] != key:
            mask = np.zeros((bounds.height, bounds.width), dtype=np.uint8)
            points = shape.pixels(width, height) - np.array([bounds.x, bounds.y])
            cv2.fillPoly(mask, [points.astype(np.int32)], 255)
            # Shrink slightly so no sliver of the table survives at the rim.
            margin = max(3, round(min(bounds.width, bounds.height) * TRAY_MASK_MARGIN)) | 1
            mask = np.asarray(cv2.erode(
                mask, cv2.getStructuringElement(cv2.MORPH_RECT, (margin, margin))
            ), dtype=np.uint8)
            self._mask_cache = (key, mask)
        return bounds, self._mask_cache[1]

    def crop(self, frame: Frame) -> Frame:
        """The image that is recognized and sent to the vision model."""
        region, mask = self.region(frame)
        cropped = region.crop(frame)
        if mask is None:
            return cropped
        masked = cropped.copy()
        masked[mask == 0] = 0  # outside the tray: black like the tray itself
        return masked

    def _update_tray(self, frame: Frame) -> None:
        if not self.tray_detection:
            return
        acquiring = self.tray.shape is None
        between_throws = self.session.state in {
            RecognitionState.WAITING, RecognitionState.REMOVAL_WAIT,
        }
        # Once an outline is in use it only changes between throws.
        if not acquiring and not between_throws:
            return
        if self._last_motion > self.session.config.stationary_threshold:
            return
        if not acquiring and self._frame_index % TRAY_UPDATE_INTERVAL_FRAMES != 0:
            return
        search = self.roi.to_pixels(frame)
        height, width = frame.shape[:2]
        if not self.tray.observe(search.crop(frame), search, (width, height), now=monotonic()):
            return
        self._previous_frame = None  # the crop changed; restart the motion baseline
        if between_throws:
            # Positions inside the new crop differ, so a die lying in the tray
            # must not look like a new throw.
            self.session.require_motion()
        else:
            # First outline found mid-cycle (just after start): what was seen
            # through the table corners is discarded.
            self.session.ignore_present_dice()

    def process(
        self, frame: Frame, *, monotonic_time: float, observe: bool = True
    ) -> RecognitionFrameResult:
        """Detect dice in one frame and, when ``observe`` is true, advance the session.

        With ``observe=False`` the frame still updates motion and detections (for the
        preview) but the recognition session does not change.
        """
        self._frame_index += 1
        self._update_tray(frame)
        cropped = self.crop(frame)
        motion_score = (
            frame_motion_score(self._previous_frame, cropped)
            if self._previous_frame is not None
            and self._previous_frame.shape == cropped.shape
            else 0.0
        )
        self._last_motion = motion_score
        detections, white_mask, rejected_count = self.detector.detect(cropped)
        diagonal = hypot(cropped.shape[1], cropped.shape[0])
        observations = tuple(
            DieObservation(
                value=detection.value,
                detector_confidence=detection.confidence,
                center=(float(detection.center[0]), float(detection.center[1])),
                pip_size_consistency=detection.pip_size_consistency,
                single_pip_center_offset=detection.single_pip_center_offset,
                pip_merge_suspected=detection.pip_merge_suspected,
                pip_recounted=detection.pip_recounted,
                pip_layout_error=detection.pip_layout_error,
            )
            for detection in detections
        )
        if observe:
            snapshot = self.session.observe(FrameObservation(
                monotonic_time=monotonic_time,
                motion_score=motion_score,
                detections=observations,
                rejected_count=rejected_count,
                roi_diagonal=diagonal,
            ))
        else:
            snapshot = self.session.snapshot()
        self._previous_frame = cropped.copy()
        rejected = tuple(
            getattr(self.detector, "last_rejected_detections", ())
        )
        pip_debug = tuple(getattr(self.detector, "last_pip_debug", ()))
        return RecognitionFrameResult(
            snapshot,
            motion_score,
            tuple(detections),
            rejected_count,
            white_mask,
            rejected,
            pip_debug,
        )

    def camera_disconnected(self, *, monotonic_time: float) -> RecognitionSnapshot:
        self._previous_frame = None
        return self.session.observe(FrameObservation(
            monotonic_time=monotonic_time,
            motion_score=0.0,
            camera_connected=False,
        ))

    def reset_frames(self) -> None:
        self._previous_frame = None
