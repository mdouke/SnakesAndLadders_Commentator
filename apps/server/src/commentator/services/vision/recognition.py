"""Hardware-independent temporal state machine for one-die recognition."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from math import dist
from uuid import uuid4


class RecognitionState(StrEnum):
    WAITING = "waiting"
    MOVING = "moving"
    STATIONARY = "stationary"
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    NEEDS_CONFIRMATION = "needs_confirmation"
    REMOVAL_WAIT = "removal_wait"
    RECOGNITION_ERROR = "recognition_error"
    CAMERA_ERROR = "camera_error"


class ConfirmationSource(StrEnum):
    AUTOMATIC = "automatic"
    REVIEWED = "reviewed"
    CORRECTED = "corrected"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class RecognitionConfig:
    movement_threshold: float = 0.08
    stationary_threshold: float = 0.02
    stationary_frames: int = 10
    consecutive_value_frames: int = 3
    candidate_window_frames: int = 5
    removal_frames: int = 3
    automatic_threshold: float = 0.85
    confirmation_threshold: float = 0.60
    recognition_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not 0 <= self.stationary_threshold < self.movement_threshold <= 1:
            raise ValueError("動きと静止のしきい値が不正です")
        if min(
            self.stationary_frames,
            self.consecutive_value_frames,
            self.candidate_window_frames,
            self.removal_frames,
        ) <= 0:
            raise ValueError("フレーム数は正の整数で指定してください")
        if self.candidate_window_frames < self.consecutive_value_frames:
            raise ValueError("候補窓は連続判定フレーム数以上にしてください")
        if not 0 <= self.confirmation_threshold <= self.automatic_threshold <= 1:
            raise ValueError("確定信頼度のしきい値が不正です")
        if self.recognition_timeout_seconds <= 0:
            raise ValueError("認識タイムアウトは正の値にしてください")


@dataclass(frozen=True, slots=True)
class DieObservation:
    value: int
    detector_confidence: float
    center: tuple[float, float]

    def __post_init__(self) -> None:
        if not 1 <= self.value <= 6:
            raise ValueError("出目は1〜6で指定してください")
        if not 0 <= self.detector_confidence <= 1:
            raise ValueError("検出信頼度は0〜1で指定してください")


@dataclass(frozen=True, slots=True)
class FrameObservation:
    monotonic_time: float
    motion_score: float
    detections: tuple[DieObservation, ...] = ()
    rejected_count: int = 0
    roi_diagonal: float = 1.0
    camera_connected: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.motion_score <= 1:
            raise ValueError("動き量は0〜1で指定してください")
        if self.roi_diagonal <= 0:
            raise ValueError("ROIの対角長は正の値にしてください")
        if self.rejected_count < 0:
            raise ValueError("却下候補数は0以上にしてください")


@dataclass(frozen=True, slots=True)
class ConfidenceBreakdown:
    detector: float
    value_stability: float
    stillness: float
    position_stability: float
    total: float


@dataclass(frozen=True, slots=True)
class Confirmation:
    id: str
    session_id: str
    sequence: int
    value: int
    source: ConfirmationSource
    confidence: ConfidenceBreakdown | None
    monotonic_time: float
    original_candidate: int | None = None


@dataclass(frozen=True, slots=True)
class DiscardedCandidate:
    value: int
    confidence: ConfidenceBreakdown
    monotonic_time: float


@dataclass(frozen=True, slots=True)
class RecognitionSnapshot:
    state: RecognitionState
    candidate_value: int | None
    confidence: ConfidenceBreakdown | None
    confirmation: Confirmation | None
    discarded_candidate: DiscardedCandidate | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _CandidateSample:
    observation: DieObservation
    motion_score: float
    roi_diagonal: float


class DiceRecognitionSession:
    """Convert frame observations into at-most-once dice confirmations."""

    def __init__(
        self,
        config: RecognitionConfig | None = None,
        *,
        session_id: str | None = None,
    ) -> None:
        self.config = config or RecognitionConfig()
        self.session_id = session_id or str(uuid4())
        self.state = RecognitionState.WAITING
        self._stationary_count = 0
        self._absence_count = 0
        self._candidate_value: int | None = None
        self._confidence: ConfidenceBreakdown | None = None
        self._confirmation: Confirmation | None = None
        self._discarded_candidate: DiscardedCandidate | None = None
        self._confirmation_counter = 0
        self._cycle_started_time: float | None = None
        self._last_observation_time: float | None = None
        self._recognition_error_code: str | None = None
        self._die_seen_in_cycle = False
        self._samples: deque[_CandidateSample] = deque(
            maxlen=self.config.candidate_window_frames
        )

    def observe(self, frame: FrameObservation) -> RecognitionSnapshot:
        self._discarded_candidate = None
        if (
            self._last_observation_time is not None
            and frame.monotonic_time < self._last_observation_time
        ):
            raise ValueError("観測時刻は単調増加で指定してください")
        self._last_observation_time = frame.monotonic_time
        if not frame.camera_connected:
            self._reset_transient()
            self._cycle_started_time = None
            self.state = RecognitionState.CAMERA_ERROR
            return self.snapshot(error_code="camera_disconnected")
        if self.state is RecognitionState.CAMERA_ERROR:
            self._reset_cycle(RecognitionState.WAITING)
            return self.snapshot()
        if (frame.detections or frame.rejected_count) and self.state in {
            RecognitionState.MOVING,
            RecognitionState.STATIONARY,
            RecognitionState.CANDIDATE,
        }:
            self._die_seen_in_cycle = True
        if self._recognition_timed_out(frame.monotonic_time):
            self._reset_transient()
            self.state = RecognitionState.RECOGNITION_ERROR
            self._recognition_error_code = "recognition_timeout"
            return self.snapshot(error_code="recognition_timeout")
        if self.state is RecognitionState.WAITING:
            if frame.motion_score >= self.config.movement_threshold:
                self._reset_cycle(RecognitionState.MOVING)
                self._cycle_started_time = frame.monotonic_time
            return self.snapshot()
        if self.state is RecognitionState.RECOGNITION_ERROR:
            if frame.motion_score >= self.config.movement_threshold:
                self._reset_cycle(RecognitionState.MOVING)
                self._cycle_started_time = frame.monotonic_time
            return self.snapshot()
        if self.state is RecognitionState.MOVING:
            self._observe_moving(frame.motion_score)
            return self.snapshot()
        if self.state is RecognitionState.STATIONARY:
            self._observe_stationary(frame)
            return self.snapshot()
        if self.state is RecognitionState.REMOVAL_WAIT:
            self._observe_removal(frame)
            return self.snapshot()
        return self.snapshot()

    def confirm_candidate(
        self, *, value: int | None = None, monotonic_time: float
    ) -> Confirmation:
        if self.state is not RecognitionState.NEEDS_CONFIRMATION:
            raise ValueError("確認待ちの候補がありません")
        candidate = self._require_candidate()
        selected = candidate if value is None else value
        self._validate_value(selected)
        source = (
            ConfirmationSource.REVIEWED
            if selected == candidate
            else ConfirmationSource.CORRECTED
        )
        return self._set_confirmation(
            selected, source, monotonic_time, original_candidate=candidate
        )

    def reject_candidate(self) -> None:
        if self.state is not RecognitionState.NEEDS_CONFIRMATION:
            raise ValueError("確認待ちの候補がありません")
        self._samples.clear()
        self._confirmation = None
        self.state = RecognitionState.RECOGNITION_ERROR
        self._recognition_error_code = "candidate_rejected"

    def enter_manual(self, value: int, *, monotonic_time: float) -> Confirmation:
        if self.state in {RecognitionState.CONFIRMED, RecognitionState.REMOVAL_WAIT}:
            raise ValueError("確定済みのサイコロを除去してから入力してください")
        if self.state is RecognitionState.CAMERA_ERROR:
            raise ValueError("カメラ切断状態を復旧してから入力してください")
        self._validate_value(value)
        return self._set_confirmation(
            value, ConfirmationSource.MANUAL, monotonic_time,
            original_candidate=self._candidate_value,
        )

    def acknowledge_committed(self, confirmation_id: str) -> None:
        if self.state is not RecognitionState.CONFIRMED or self._confirmation is None:
            raise ValueError("ゲームへ登録できる確定結果がありません")
        if confirmation_id != self._confirmation.id:
            raise ValueError("確定IDが一致しません")
        self.state = RecognitionState.REMOVAL_WAIT
        self._absence_count = 0
        self._cycle_started_time = None

    def discard_confirmation(self, confirmation_id: str) -> None:
        """Return a failed game registration to operator confirmation."""
        if self.state is not RecognitionState.CONFIRMED or self._confirmation is None:
            raise ValueError("取り消せる確定結果がありません")
        if confirmation_id != self._confirmation.id:
            raise ValueError("確定IDが一致しません")
        self._confirmation = None
        if self._candidate_value is not None:
            self.state = RecognitionState.NEEDS_CONFIRMATION
        else:
            self.state = RecognitionState.RECOGNITION_ERROR
            self._recognition_error_code = "confirmation_discarded"

    def snapshot(self, *, error_code: str | None = None) -> RecognitionSnapshot:
        if error_code is None:
            if self.state is RecognitionState.CAMERA_ERROR:
                error_code = "camera_disconnected"
            elif self.state is RecognitionState.RECOGNITION_ERROR:
                error_code = self._recognition_error_code
        return RecognitionSnapshot(
            state=self.state,
            candidate_value=self._candidate_value,
            confidence=self._confidence,
            confirmation=self._confirmation,
            discarded_candidate=self._discarded_candidate,
            error_code=error_code,
        )

    def _observe_moving(self, motion_score: float) -> None:
        if motion_score <= self.config.stationary_threshold:
            self._stationary_count += 1
            if self._stationary_count >= self.config.stationary_frames:
                self._reset_cycle(RecognitionState.STATIONARY)
        else:
            self._stationary_count = 0

    def _observe_stationary(self, frame: FrameObservation) -> None:
        if frame.motion_score >= self.config.movement_threshold:
            self._reset_cycle(RecognitionState.MOVING)
            self._cycle_started_time = frame.monotonic_time
            return
        if len(frame.detections) != 1:
            self._samples.clear()
            self._candidate_value = None
            if not frame.detections and frame.rejected_count == 0:
                self._absence_count += 1
                if self._absence_count >= self.config.removal_frames:
                    self._reset_cycle(RecognitionState.WAITING)
            else:
                self._absence_count = 0
            return
        self._absence_count = 0
        sample = _CandidateSample(
            frame.detections[0], frame.motion_score, frame.roi_diagonal
        )
        self._samples.append(sample)
        required = self.config.consecutive_value_frames
        if len(self._samples) < required:
            return
        recent = list(self._samples)[-required:]
        values = {item.observation.value for item in recent}
        if len(values) == 1:
            self._candidate_value = recent[-1].observation.value
            self.state = RecognitionState.CANDIDATE
            self._evaluate_candidate(frame.monotonic_time)

    def _evaluate_candidate(self, monotonic_time: float) -> None:
        candidate = self._require_candidate()
        matching = [
            sample for sample in self._samples
            if sample.observation.value == candidate
        ]
        detector = sum(
            sample.observation.detector_confidence for sample in matching
        ) / len(matching)
        value_stability = len(matching) / len(self._samples)
        stillness = sum(
            max(
                0.0,
                1.0 - sample.motion_score / max(self.config.stationary_threshold, 1e-9),
            )
            for sample in matching
        ) / len(matching)
        centers = [sample.observation.center for sample in matching]
        mean_center = (
            sum(point[0] for point in centers) / len(centers),
            sum(point[1] for point in centers) / len(centers),
        )
        maximum_offset = max(dist(point, mean_center) for point in centers)
        diagonal = max(sample.roi_diagonal for sample in matching)
        position_stability = max(0.0, 1.0 - maximum_offset / diagonal)
        total = (
            0.45 * detector
            + 0.30 * value_stability
            + 0.15 * stillness
            + 0.10 * position_stability
        )
        self._confidence = ConfidenceBreakdown(
            detector=detector,
            value_stability=value_stability,
            stillness=stillness,
            position_stability=position_stability,
            total=total,
        )
        if total >= self.config.automatic_threshold:
            self._set_confirmation(
                candidate, ConfirmationSource.AUTOMATIC, monotonic_time,
                original_candidate=candidate,
            )
        elif total >= self.config.confirmation_threshold:
            self.state = RecognitionState.NEEDS_CONFIRMATION
        else:
            discarded = DiscardedCandidate(candidate, self._confidence, monotonic_time)
            self._reset_cycle(RecognitionState.STATIONARY)
            self._discarded_candidate = discarded

    def _observe_removal(self, frame: FrameObservation) -> None:
        if frame.motion_score >= self.config.movement_threshold:
            self._reset_cycle(RecognitionState.MOVING)
            self._cycle_started_time = frame.monotonic_time
            return
        if frame.detections:
            self._absence_count = 0
            return
        self._absence_count += 1
        if self._absence_count >= self.config.removal_frames:
            self._reset_cycle(RecognitionState.WAITING)

    def _set_confirmation(
        self,
        value: int,
        source: ConfirmationSource,
        monotonic_time: float,
        *,
        original_candidate: int | None,
    ) -> Confirmation:
        self._confirmation_counter += 1
        self._confirmation = Confirmation(
            id=f"{self.session_id}:{self._confirmation_counter}",
            session_id=self.session_id,
            sequence=self._confirmation_counter,
            value=value,
            source=source,
            confidence=self._confidence,
            monotonic_time=monotonic_time,
            original_candidate=original_candidate,
        )
        self.state = RecognitionState.CONFIRMED
        return self._confirmation

    def _reset_cycle(self, state: RecognitionState) -> None:
        self._reset_transient()
        self.state = state
        if state is RecognitionState.WAITING:
            self._cycle_started_time = None

    def _reset_transient(self) -> None:
        self._stationary_count = 0
        self._absence_count = 0
        self._candidate_value = None
        self._confidence = None
        self._confirmation = None
        self._discarded_candidate = None
        self._samples.clear()
        self._recognition_error_code = None
        self._die_seen_in_cycle = False

    def _require_candidate(self) -> int:
        if self._candidate_value is None:
            raise RuntimeError("候補の出目がありません")
        return self._candidate_value

    def _recognition_timed_out(self, monotonic_time: float) -> bool:
        return (
            self.state
            in {
                RecognitionState.MOVING,
                RecognitionState.STATIONARY,
                RecognitionState.CANDIDATE,
            }
            and self._cycle_started_time is not None
            and self._die_seen_in_cycle
            and monotonic_time - self._cycle_started_time
            >= self.config.recognition_timeout_seconds
        )

    @staticmethod
    def _validate_value(value: int) -> None:
        if not 1 <= value <= 6:
            raise ValueError("出目は1〜6で指定してください")
