"""Testable control and recording logic for the live vision bench."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import IO, Any, Protocol, cast

import cv2
import numpy as np

from commentator.services.vision.camera import (
    CameraStream,
    OpenCVCameraProvider,
)
from commentator.services.vision.detector import (
    DiceDetector,
    PipContourDebug,
    PipDetectionDebug,
)
from commentator.services.vision.motion import Frame
from commentator.services.vision.pipeline import (
    DiceRecognitionPipeline,
    RecognitionFrameResult,
)
from commentator.services.vision.recognition import (
    DiceRecognitionSession,
    RecognitionConfig,
    RecognitionSnapshot,
    RecognitionState,
)
from commentator.services.vision.settings import (
    VisionSettings,
    VisionSettingsStore,
)


class VideoWriter(Protocol):
    def isOpened(self) -> bool: ...
    def write(self, frame: Frame) -> None: ...
    def release(self) -> None: ...


WriterFactory = Callable[[Path, float, tuple[int, int]], VideoWriter]


def _opencv_writer(path: Path, fps: float, size: tuple[int, int]) -> VideoWriter:
    fourcc = cast(Any, cv2).VideoWriter_fourcc(*"MJPG")
    return cast(VideoWriter, cv2.VideoWriter(str(path), fourcc, fps, size))


def _settings_payload(settings: VisionSettings) -> dict[str, Any]:
    return {
        "cameraId": settings.camera_id,
        "roi": asdict(settings.roi),
        "whiteThreshold": settings.white_threshold,
        "pipThreshold": settings.pip_threshold,
        "cameraResolution": {
            "width": settings.camera_width,
            "height": settings.camera_height,
        },
        "exposure": settings.exposure,
        "automaticThreshold": settings.automatic_threshold,
        "confirmationThreshold": settings.confirmation_threshold,
        "stationaryFrames": settings.stationary_frames,
        "consecutiveValueFrames": settings.consecutive_value_frames,
        "minPipSizeConsistency": settings.min_pip_size_consistency,
        "maxSinglePipCenterOffset": settings.max_single_pip_center_offset,
        "maxPipLayoutError": settings.max_pip_layout_error,
        "minDetectorConfidence": settings.min_detector_confidence,
    }


def _confidence_payload(snapshot: RecognitionSnapshot) -> dict[str, float] | None:
    confidence = snapshot.confidence
    if confidence is None:
        return None
    return {
        "detector": confidence.detector,
        "valueStability": confidence.value_stability,
        "stillness": confidence.stillness,
        "positionStability": confidence.position_stability,
        "pipSizeConsistency": confidence.pip_size_consistency,
        "singlePipCenterOffset": confidence.single_pip_center_offset,
        "pipMergeSuspected": confidence.pip_merge_suspected,
        "pipRecounted": confidence.pip_recounted,
        "pipLayoutError": confidence.pip_layout_error,
        "total": confidence.total,
    }


@dataclass(frozen=True, slots=True)
class RecordingPaths:
    video: Path
    frames: Path
    events: Path
    labels: Path
    settings: Path


@dataclass(slots=True)
class _PendingRoll:
    id: str
    start_seconds: float
    expected_value: int | None = None


class VisionBenchRecorder:
    """Write ROI video and synchronized sidecars only while explicitly active."""

    def __init__(
        self,
        root: Path,
        *,
        writer_factory: WriterFactory = _opencv_writer,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.root = root
        self._writer_factory = writer_factory
        self._now = now
        self.paths: RecordingPaths | None = None
        self._writer: VideoWriter | None = None
        self._frames_file: IO[str] | None = None
        self._events_file: IO[str] | None = None
        self._started_at = 0.0
        self._frame_index = 0
        self._frame_size: tuple[int, int] | None = None
        self._previous_state: RecognitionState | None = None
        self._active_roll: _PendingRoll | None = None
        self._roll_sequence = 0
        self._rolls: list[dict[str, Any]] = []
        self.unlabeled_rolls = 0
        self.automatic_confirmations = 0
        self.review_candidates = 0

    @property
    def active(self) -> bool:
        return self._writer is not None

    @property
    def rolls_started(self) -> int:
        return self._roll_sequence

    @property
    def labeled_rolls(self) -> int:
        active_labeled = int(
            self._active_roll is not None
            and self._active_roll.expected_value is not None
        )
        return len(self._rolls) + active_labeled

    @property
    def rolls_without_label(self) -> int:
        return self.rolls_started - self.labeled_rolls

    def start(
        self,
        frame: Frame,
        *,
        monotonic_time: float,
        settings: VisionSettings,
        fps: float = 30.0,
    ) -> RecordingPaths:
        if self.active:
            raise ValueError("録画はすでに開始されています")
        if frame.size == 0:
            raise ValueError("録画するフレームがありません")
        stamp = self._now()
        directory = self.root / stamp.strftime("%Y%m%d")
        directory.mkdir(parents=True, exist_ok=True)
        base = directory / f"vision-bench-{stamp.strftime('%Y%m%d-%H%M%S')}"
        paths = RecordingPaths(
            video=base.with_suffix(".avi"),
            frames=base.with_suffix(".frames.jsonl"),
            events=base.with_suffix(".events.jsonl"),
            labels=base.with_suffix(".labels.json"),
            settings=base.with_suffix(".settings.json"),
        )
        height, width = frame.shape[:2]
        writer = self._writer_factory(paths.video, fps, (width, height))
        if not writer.isOpened():
            writer.release()
            raise ValueError("録画ファイルを作成できません")
        self.paths = paths
        self._writer = writer
        self._frames_file = paths.frames.open("w", encoding="utf-8")
        self._events_file = paths.events.open("w", encoding="utf-8")
        paths.settings.write_text(
            json.dumps(_settings_payload(settings), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._started_at = monotonic_time
        self._frame_index = 0
        self._frame_size = (width, height)
        self._previous_state = None
        self._active_roll = None
        self._roll_sequence = 0
        self._rolls = []
        self.unlabeled_rolls = 0
        self.automatic_confirmations = 0
        self.review_candidates = 0
        return paths

    def record(
        self,
        frame: Frame,
        *,
        monotonic_time: float,
        result: RecognitionFrameResult,
    ) -> None:
        if not self.active:
            return
        assert self._writer is not None
        assert self._frames_file is not None
        assert self._events_file is not None
        seconds = max(0.0, monotonic_time - self._started_at)
        output = frame
        if self._frame_size is not None and (frame.shape[1], frame.shape[0]) != self._frame_size:
            output = cast(
                Frame,
                cv2.resize(frame, self._frame_size, interpolation=cv2.INTER_AREA),
            )
        self._writer.write(output)
        self._write_jsonl(self._frames_file, {
            "frameIndex": self._frame_index,
            "seconds": seconds,
            "monotonicTime": monotonic_time,
        })
        state = result.snapshot.state
        if state is not self._previous_state:
            self._write_jsonl(self._events_file, {
                "seconds": seconds,
                "type": "state",
                "state": state.value,
                "candidateValue": result.snapshot.candidate_value,
                "confidence": _confidence_payload(result.snapshot),
                "motionScore": result.motion_score,
                "rejectedCount": result.rejected_count,
            })
            self._update_roll_interval(state, seconds)
            self._previous_state = state
        self._frame_index += 1

    def record_decision(
        self,
        *,
        monotonic_time: float,
        kind: str,
        value: int,
        confidence: dict[str, float] | None,
    ) -> None:
        if not self.active or self._events_file is None:
            return
        if kind == "automatic":
            self.automatic_confirmations += 1
        elif kind == "needs_confirmation":
            self.review_candidates += 1
        self._write_jsonl(self._events_file, {
            "seconds": max(0.0, monotonic_time - self._started_at),
            "type": "decision",
            "decisionType": kind,
            "value": value,
            "confidence": confidence,
        })

    def label_current(self, value: int) -> None:
        if not 1 <= value <= 6:
            raise ValueError("正解は1〜6で指定してください")
        if not self.active:
            raise ValueError("正解入力の前に録画を開始してください")
        if self._active_roll is None:
            raise ValueError("投擲開始を検出してから正解を入力してください")
        self._active_roll.expected_value = value

    def stop(self, *, monotonic_time: float) -> RecordingPaths:
        if not self.active or self.paths is None:
            raise ValueError("録画は開始されていません")
        seconds = max(0.0, monotonic_time - self._started_at)
        self._close_roll(seconds)
        paths = self.paths
        paths.labels.write_text(
            json.dumps({"rolls": self._rolls}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        assert self._writer is not None
        assert self._frames_file is not None
        assert self._events_file is not None
        self._writer.release()
        self._frames_file.close()
        self._events_file.close()
        self._writer = None
        self._frames_file = None
        self._events_file = None
        self._active_roll = None
        self._frame_size = None
        return paths

    def _update_roll_interval(self, state: RecognitionState, seconds: float) -> None:
        if state is RecognitionState.MOVING:
            self._close_roll(seconds)
            self._roll_sequence += 1
            self._active_roll = _PendingRoll(
                id=f"roll-{self._roll_sequence:03d}",
                start_seconds=seconds,
            )
        elif state is RecognitionState.WAITING:
            self._close_roll(seconds)

    def _close_roll(self, seconds: float) -> None:
        roll = self._active_roll
        if roll is None:
            return
        if roll.expected_value is None:
            self.unlabeled_rolls += 1
        elif seconds > roll.start_seconds:
            self._rolls.append({
                "id": roll.id,
                "expectedValue": roll.expected_value,
                "startSeconds": roll.start_seconds,
                "endSeconds": seconds,
            })
        self._active_roll = None

    @staticmethod
    def _write_jsonl(stream: IO[str], payload: dict[str, Any]) -> None:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stream.flush()


@dataclass(frozen=True, slots=True)
class BenchFrame:
    full_preview: Frame
    roi_preview: Frame
    mask: Frame
    pip_debug_preview: Frame | None
    result: RecognitionFrameResult
    fps: float


def _pip_rejection_label(contour: PipContourDebug) -> str:
    reason = contour.rejection_reason
    if reason == "area_small":
        return f"area< {contour.area_ratio:.3f}"
    if reason == "area_large":
        return f"area> {contour.area_ratio:.3f}"
    if reason == "circularity":
        return f"circ {contour.circularity:.2f}"
    if reason == "solidity":
        return f"solid {contour.solidity:.2f}"
    if reason == "aspect":
        return f"aspect {contour.aspect_ratio:.2f}"
    return "rejected"


def _merge_label(item: PipDetectionDebug) -> str:
    if item.pip_recounted:
        return "recount"
    return "yes" if item.pip_merge_suspected else "no"


def render_pip_debug(items: tuple[PipDetectionDebug, ...]) -> Frame | None:
    """Render the exact pip-counting inputs and decisions for the live GUI."""
    rows: list[Frame] = []
    for item in items:
        face = cv2.cvtColor(item.warped_gray, cv2.COLOR_GRAY2BGR)
        mask_gray = np.full(item.warped_gray.shape, 192, dtype=np.uint8)
        inner_height, inner_width = item.pip_mask.shape[:2]
        margin = item.margin
        mask_gray[
            margin : margin + inner_height,
            margin : margin + inner_width,
        ] = 255 - item.pip_mask
        mask = cv2.cvtColor(mask_gray, cv2.COLOR_GRAY2BGR)
        for contour in item.contours:
            color = (0, 190, 0) if contour.accepted else (0, 0, 255)
            cv2.polylines(face, [contour.contour], True, color, 2, cv2.LINE_AA)
            cv2.polylines(mask, [contour.contour], True, color, 2, cv2.LINE_AA)
            if not contour.accepted:
                x, y, _, _ = cv2.boundingRect(contour.contour)
                cv2.putText(
                    face,
                    _pip_rejection_label(contour),
                    (max(0, x - 3), max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.34,
                    color,
                    1,
                    cv2.LINE_AA,
                )
        header = np.zeros((28, 360, 3), dtype=np.uint8)
        cv2.putText(
            header,
            (
                f"#{item.candidate_index}  pips={item.counted_pips}  "
                f"PipThr={item.threshold_value:.0f} "
                f"{'auto' if item.threshold_is_automatic else 'fixed'}   "
                f"PipSize={item.pip_size_consistency:.2f}   "
                f"PipCenter={item.single_pip_center_offset:.2f}   "
                f"Merge={_merge_label(item)}   "
                f"Layout={item.pip_layout_error:.2f}   "
                "face / count mask"
            ),
            (5, 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        rows.append(np.vstack((header, np.hstack((face, mask)))))
    return np.vstack(rows) if rows else None


class VisionBenchEngine:
    """Own camera, recognition, recording, and non-widget operator state."""

    def __init__(
        self,
        settings_store: VisionSettingsStore,
        recording_root: Path,
        *,
        camera_provider: OpenCVCameraProvider | None = None,
        recorder: VisionBenchRecorder | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings_store = settings_store
        self.settings = settings_store.load()
        self.camera_provider = camera_provider or OpenCVCameraProvider()
        self.recorder = recorder or VisionBenchRecorder(recording_root)
        self.clock = clock
        self.stream: CameraStream | None = None
        self.history: deque[str] = deque(maxlen=12)
        self.last_frame: BenchFrame | None = None
        self._frame_times: deque[float] = deque(maxlen=30)
        self._pending_recognition_config: RecognitionConfig | None = None
        self._build_pipeline()

    @property
    def connected(self) -> bool:
        return self.stream is not None

    @property
    def recognition_settings_pending(self) -> bool:
        return self._pending_recognition_config is not None

    @property
    def active_recognition_config(self) -> RecognitionConfig:
        return self.session.config

    def connect(self) -> dict[str, float | bool]:
        self.disconnect()
        camera_id = self.settings.camera_id if self.settings.camera_id is not None else 0
        stream = self.camera_provider.open(camera_id)
        self.stream = stream
        configure = getattr(stream, "configure", None)
        if callable(configure):
            return cast(dict[str, float | bool], configure(
                width=self.settings.camera_width,
                height=self.settings.camera_height,
                exposure=self.settings.exposure,
            ))
        return {}

    def disconnect(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.pipeline.reset_frames()

    def update_settings(self, settings: VisionSettings) -> None:
        previous = self.settings
        self.settings = settings
        visual_changed = self._visual_settings(previous) != self._visual_settings(settings)
        recognition_changed = (
            previous.recognition_config() != settings.recognition_config()
        )
        if visual_changed:
            self._pending_recognition_config = None
            self._build_pipeline()
        elif recognition_changed:
            self._schedule_recognition_config(settings.recognition_config())
        if self.stream is not None:
            configure = getattr(self.stream, "configure", None)
            if callable(configure):
                configure(
                    width=settings.camera_width,
                    height=settings.camera_height,
                    exposure=settings.exposure,
                )

    def save_settings(self) -> None:
        self.settings_store.save(self.settings)

    def read(self) -> BenchFrame:
        if self.stream is None:
            raise ValueError("カメラに接続されていません")
        return self.process_frame(self.stream.read(), monotonic_time=self.clock())

    def process_frame(self, frame: Frame, *, monotonic_time: float) -> BenchFrame:
        result = self.pipeline.process(frame, monotonic_time=monotonic_time)
        roi = self.settings.roi.crop(frame)
        roi_preview = DiceDetector.annotate(roi, list(result.detections))
        for rejected in result.rejected_detections:
            cv2.polylines(roi_preview, [rejected.box], True, (0, 0, 255), 3, cv2.LINE_AA)
            for point in rejected.pip_centers:
                cv2.circle(roi_preview, point, 6, (0, 0, 255), 2, cv2.LINE_AA)
            x = int(rejected.box[:, 0].min())
            y = max(24, int(rejected.box[:, 1].min()) - 8)
            cv2.putText(
                roi_preview,
                f"#{rejected.candidate_index} REJECTED {rejected.reason}",
                (x, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 0, 255),
                2,
                cv2.LINE_AA,
            )
        full_preview = frame.copy()
        pixels = self.settings.roi.to_pixels(frame)
        full_preview[
            pixels.y : pixels.y + pixels.height,
            pixels.x : pixels.x + pixels.width,
        ] = roi_preview
        cv2.rectangle(
            full_preview,
            (pixels.x, pixels.y),
            (pixels.x + pixels.width - 1, pixels.y + pixels.height - 1),
            (0, 220, 255),
            3,
            cv2.LINE_AA,
        )
        self._frame_times.append(monotonic_time)
        fps = self._measured_fps()
        self.recorder.record(
            roi,
            monotonic_time=monotonic_time,
            result=result,
        )
        self._advance_decision(result, monotonic_time)
        self._apply_pending_recognition_config_if_safe()
        bench_frame = BenchFrame(
            full_preview,
            roi_preview,
            result.white_mask,
            render_pip_debug(result.pip_debug),
            result,
            fps,
        )
        self.last_frame = bench_frame
        return bench_frame

    def start_recording(self) -> RecordingPaths:
        if self.last_frame is None:
            raise ValueError("映像を取得してから録画を開始してください")
        return self.recorder.start(
            self.settings.roi.crop(self.last_frame.full_preview),
            monotonic_time=self.clock(),
            settings=self.settings,
            fps=max(1.0, self.last_frame.fps or 30.0),
        )

    def stop_recording(self) -> RecordingPaths:
        return self.recorder.stop(monotonic_time=self.clock())

    def label_current(self, value: int) -> None:
        self.recorder.label_current(value)
        self.history.appendleft(f"正解入力: {value}")

    def close(self) -> None:
        if self.recorder.active:
            self.recorder.stop(monotonic_time=self.clock())
        self.disconnect()

    def _build_pipeline(self) -> None:
        self.detector = DiceDetector(
            white_threshold=self.settings.white_threshold,
            pip_threshold=self.settings.pip_threshold,
            debug_pips=True,
        )
        self.session = DiceRecognitionSession(self.settings.recognition_config())
        self.pipeline = DiceRecognitionPipeline(
            detector=self.detector,
            session=self.session,
            roi=self.settings.roi,
        )
        if hasattr(self, "_frame_times"):
            self._frame_times.clear()

    def _schedule_recognition_config(self, config: RecognitionConfig) -> None:
        if self.session.state in {
            RecognitionState.WAITING,
            RecognitionState.REMOVAL_WAIT,
        }:
            self.session.config = config
            self._pending_recognition_config = None
        else:
            self._pending_recognition_config = config

    def _apply_pending_recognition_config_if_safe(self) -> None:
        pending = self._pending_recognition_config
        if pending is not None and self.session.state in {
            RecognitionState.WAITING,
            RecognitionState.REMOVAL_WAIT,
        }:
            self.session.config = pending
            self._pending_recognition_config = None

    @staticmethod
    def _visual_settings(settings: VisionSettings) -> tuple[object, ...]:
        return (
            settings.camera_id,
            settings.roi,
            settings.white_threshold,
            settings.pip_threshold,
            settings.camera_width,
            settings.camera_height,
            settings.exposure,
        )

    def _advance_decision(
        self, result: RecognitionFrameResult, monotonic_time: float
    ) -> None:
        snapshot = result.snapshot
        if (
            snapshot.state is RecognitionState.CONFIRMED
            and snapshot.confirmation is not None
        ):
            confirmation = snapshot.confirmation
            self.history.appendleft(
                f"自動確定: {confirmation.value} ({self._total(snapshot):.3f})"
            )
            self.recorder.record_decision(
                monotonic_time=monotonic_time,
                kind="automatic",
                value=confirmation.value,
                confidence=_confidence_payload(snapshot),
            )
            self.session.acknowledge_committed(confirmation.id)
        elif (
            snapshot.state is RecognitionState.NEEDS_CONFIRMATION
            and snapshot.candidate_value is not None
        ):
            self.history.appendleft(
                f"確認候補: {snapshot.candidate_value} ({self._total(snapshot):.3f})"
            )
            self.recorder.record_decision(
                monotonic_time=monotonic_time,
                kind="needs_confirmation",
                value=snapshot.candidate_value,
                confidence=_confidence_payload(snapshot),
            )
            self.session.reject_candidate()

    def _measured_fps(self) -> float:
        if len(self._frame_times) < 2:
            return 0.0
        elapsed = self._frame_times[-1] - self._frame_times[0]
        return (len(self._frame_times) - 1) / elapsed if elapsed > 0 else 0.0

    @staticmethod
    def _total(snapshot: RecognitionSnapshot) -> float:
        return snapshot.confidence.total if snapshot.confidence is not None else 0.0


def blank_mask_like(frame: Frame) -> Frame:
    """Small utility used by the GUI before the first successful detection."""
    return np.zeros(frame.shape[:2], dtype=np.uint8)
