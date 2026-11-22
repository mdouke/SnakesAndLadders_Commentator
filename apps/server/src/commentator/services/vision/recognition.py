"""Hardware-independent temporal state machine for one-die recognition."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from math import dist
from statistics import median
from typing import Any, Literal
from uuid import uuid4

# How the value is read (ADR 0006). Motion and stillness always come from OpenCV.
ReadingMode = Literal["opencv", "vlm", "hybrid"]
READING_MODES: tuple[ReadingMode, ...] = ("opencv", "vlm", "hybrid")


class RecognitionState(StrEnum):
    WAITING = "waiting"
    MOVING = "moving"
    STATIONARY = "stationary"
    CANDIDATE = "candidate"
    READING = "reading"  # waiting for the vision model (vlm / hybrid modes)
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
    automatic_threshold: float = 0.83
    confirmation_threshold: float = 0.60
    recognition_timeout_seconds: float = 10.0
    min_pip_size_consistency: float = 0.50
    max_single_pip_center_offset: float = 0.22
    max_pip_layout_error: float = 0.15
    min_detector_confidence: float = 0.78

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
        if not 0 <= self.min_pip_size_consistency <= 1:
            raise ValueError("目の大きさの均一性しきい値は0〜1で指定してください")
        if not 0 <= self.max_single_pip_center_offset <= 1:
            raise ValueError("1の目の中心距離上限は0〜1で指定してください")
        if not 0 <= self.max_pip_layout_error <= 1:
            raise ValueError("目の配置ずれ上限は0〜1で指定してください")
        if not 0 <= self.min_detector_confidence <= 1:
            raise ValueError("外形信頼度の下限は0〜1で指定してください")


@dataclass(frozen=True, slots=True)
class DieObservation:
    value: int
    detector_confidence: float
    center: tuple[float, float]
    pip_size_consistency: float = 1.0
    single_pip_center_offset: float = 0.0
    pip_merge_suspected: bool = False
    pip_recounted: bool = False
    pip_layout_error: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= self.value <= 6:
            raise ValueError("出目は1〜6で指定してください")
        if not 0 <= self.detector_confidence <= 1:
            raise ValueError("検出信頼度は0〜1で指定してください")
        if not 0 <= self.pip_size_consistency <= 1:
            raise ValueError("目の大きさの均一性は0〜1で指定してください")
        if not 0 <= self.single_pip_center_offset <= 1:
            raise ValueError("1の目の中心距離は0〜1で指定してください")
        if self.pip_layout_error < 0:
            raise ValueError("目の配置ずれは0以上で指定してください")


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
    pip_size_consistency: float = 1.0
    single_pip_center_offset: float = 0.0
    pip_merge_suspected: bool = False
    pip_recounted: bool = False
    pip_layout_error: float = 0.0


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
    # How the value was read: recognizer, OpenCV candidate and the model's answer.
    recognition: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class VisionReading:
    """The vision model's answer for one still image (see ``vision/vlm.py``)."""

    dice_count: int
    value: int | None  # None when the top face could not be read
    confidence: Literal["high", "medium", "low"]
    note: str = ""
    model: str = ""
    duration_ms: int = 0

    @property
    def certain_single(self) -> bool:
        return self.dice_count == 1 and self.value is not None and self.confidence == "high"


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
    candidate_id: str | None = None
    error_code: str | None = None
    reading_id: str | None = None
    recognition: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _CandidateSample:
    observation: DieObservation
    motion_score: float
    roi_diagonal: float


class DiceRecognitionSession:
    """Convert frame observations into at-most-once dice confirmations."""

    _DUPLICATE_POSITION_RATIO = 0.05
    # A die that was lying in the tray at (re)start and has been out of sight
    # this long was picked up; after that the same spot may hold a new throw.
    _RESTING_DIE_REMOVED_SECONDS = 1.0

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
        self._candidate_id: str | None = None
        self._candidate_counter = 0
        self._confidence: ConfidenceBreakdown | None = None
        self._confirmation: Confirmation | None = None
        self._discarded_candidate: DiscardedCandidate | None = None
        self._confirmation_counter = 0
        self._cycle_started_time: float | None = None
        self._last_observation_time: float | None = None
        self._recognition_error_code: str | None = None
        self._die_seen_in_cycle = False
        self._last_confirmed_center: tuple[float, float] | None = None
        self._last_confirmed_roi_diagonal = 1.0
        self._motion_required = False
        # The die lying in the tray when play (re)started; see ignore_present_dice().
        self._guard_present_die = False
        self._resting_die: tuple[tuple[float, float], int, float] | None = None
        self._resting_absent_since: float | None = None
        self._settle_frames_left = 0
        self._samples: deque[_CandidateSample] = deque(
            maxlen=self.config.candidate_window_frames
        )
        self.reading_mode: ReadingMode = "opencv"
        self._reading_id: str | None = None
        self._reading_counter = 0
        self._opencv_candidate: int | None = None
        self._opencv_automatic = False
        self._recognition: dict[str, Any] | None = None

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
        self._track_resting_die(frame)
        if self._settle_frames_left > 0 and self.state in {
            RecognitionState.WAITING, RecognitionState.REMOVAL_WAIT,
        }:
            # Just (re)started: the camera may still be adjusting its exposure.
            # Motion counts only after the picture has been still for a moment.
            if frame.motion_score <= self.config.stationary_threshold:
                self._settle_frames_left -= 1
            else:
                self._settle_frames_left = self.config.stationary_frames
            self._remember_resting_die(frame)
            return self.snapshot()
        if self._recognition_timed_out(frame.monotonic_time):
            self._reset_transient()
            self.state = RecognitionState.RECOGNITION_ERROR
            self._recognition_error_code = "recognition_timeout"
            return self.snapshot(error_code="recognition_timeout")
        if self.state is RecognitionState.WAITING:
            self._remember_resting_die(frame)
            die_visible = bool(frame.detections or frame.rejected_count)
            moved = frame.motion_score >= self.config.movement_threshold
            new_position = (
                not self._motion_required
                and die_visible
                and self._die_is_away_from_last_confirmation(frame)
            )
            if moved or new_position:
                self._motion_required = False
                self._last_confirmed_center = None
                self._reset_cycle(RecognitionState.MOVING)
                self._cycle_started_time = frame.monotonic_time
                self._die_seen_in_cycle = die_visible
            return self.snapshot()
        if self.state is RecognitionState.RECOGNITION_ERROR:
            if frame.motion_score >= self.config.movement_threshold:
                self._last_confirmed_center = None
                self._reset_cycle(RecognitionState.MOVING)
                self._cycle_started_time = frame.monotonic_time
            return self.snapshot()
        if self.state is RecognitionState.MOVING:
            self._observe_moving(frame)
            return self.snapshot()
        if self.state is RecognitionState.STATIONARY:
            self._observe_stationary(frame)
            return self.snapshot()
        if self.state is RecognitionState.REMOVAL_WAIT:
            self._observe_removal(frame)
            return self.snapshot()
        if self.state is RecognitionState.READING:
            if frame.motion_score >= self.config.movement_threshold:
                # The die moved while the model was reading: that answer is stale.
                self._reset_cycle(RecognitionState.MOVING)
                self._cycle_started_time = frame.monotonic_time
            return self.snapshot()
        return self.snapshot()

    def complete_reading(
        self,
        reading_id: str,
        reading: VisionReading | None,
        *,
        monotonic_time: float,
        error_code: str | None = None,
    ) -> bool:
        """Apply the model's answer; returns False when it is no longer current.

        ``reading`` is None when the call failed (``error_code`` says why).
        """
        if self.state is not RecognitionState.READING or reading_id != self._reading_id:
            return False
        self._reading_id = None
        recognition: dict[str, Any] = {
            "recognizer": self.reading_mode,
            "readingId": reading_id,
            "opencvCandidate": self._opencv_candidate,
            "vlm": _reading_dict(reading) if reading is not None else None,
            "vlmError": error_code if reading is None else None,
        }
        self._recognition = recognition
        if self.reading_mode == "hybrid":
            self._decide_hybrid(reading, monotonic_time)
        else:
            self._decide_vlm(reading, monotonic_time, error_code)
        return True

    def _decide_vlm(
        self, reading: VisionReading | None, monotonic_time: float, error_code: str | None
    ) -> None:
        if reading is None:
            self._fail("vlm_failed" if error_code is None else f"vlm_{error_code}")
        elif reading.dice_count == 0:
            # Nothing was thrown (a hand moved, for example). Wait for the next throw.
            self._reset_cycle(RecognitionState.WAITING)
        elif reading.value is None:
            self._fail("vlm_unreadable")
        elif reading.certain_single:
            self._set_candidate(reading.value)
            self._set_confirmation(
                reading.value, ConfirmationSource.AUTOMATIC, monotonic_time,
                original_candidate=reading.value,
            )
        else:
            self._set_candidate(reading.value)
            self.state = RecognitionState.NEEDS_CONFIRMATION

    def _decide_hybrid(self, reading: VisionReading | None, monotonic_time: float) -> None:
        opencv = self._opencv_candidate
        assert opencv is not None
        agreed = reading is not None and reading.dice_count == 1 and reading.value == opencv
        if reading is None:
            # The model is unavailable: decide exactly as the OpenCV mode would.
            automatic = self._opencv_automatic
            candidate = opencv
        elif agreed:
            automatic = reading.certain_single or self._opencv_automatic
            candidate = opencv
        else:
            automatic = False
            # Disagreement: suggest the model's value only when it is sure.
            candidate = reading.value if reading.certain_single and reading.value else opencv
        self._set_candidate(candidate)
        if automatic:
            self._set_confirmation(
                candidate, ConfirmationSource.AUTOMATIC, monotonic_time,
                original_candidate=opencv,
            )
        else:
            self.state = RecognitionState.NEEDS_CONFIRMATION

    def _begin_reading(self) -> None:
        self._reading_counter += 1
        self._reading_id = f"{self.session_id}:reading:{self._reading_counter}"
        self.state = RecognitionState.READING

    def _set_candidate(self, value: int) -> None:
        if self._candidate_value != value or self._candidate_id is None:
            self._candidate_counter += 1
            self._candidate_id = f"{self.session_id}:candidate:{self._candidate_counter}"
        self._candidate_value = value

    def _fail(self, code: str) -> None:
        self.state = RecognitionState.RECOGNITION_ERROR
        self._recognition_error_code = code

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

    def ignore_present_dice(self) -> None:
        """Begin in ``removal_wait`` so a die already lying in the ROI is not read.

        Used when a game starts or resumes while the camera is running. The die must
        disappear for ``removal_frames`` or move clearly before a new throw counts.
        """
        self._last_confirmed_center = None
        self._cycle_started_time = None
        self._reset_cycle(RecognitionState.REMOVAL_WAIT)
        # The die's position is unknown, so a die that "reappears" after a few missed
        # detections cannot be told apart from it. Only clear motion starts a throw.
        self._motion_required = True
        # Motion right after (re)start (the camera adjusting its exposure, a hand
        # passing) must not make the die lying there look like a new throw: wait
        # until the picture is still, remember where that die lies, and do not
        # read a die that comes to rest at the same spot with the same value.
        self._guard_present_die = True
        self._resting_die = None
        self._resting_absent_since = None
        self._settle_frames_left = self.config.stationary_frames

    @property
    def guarding_present_die(self) -> bool:
        """True until the die present at (re)start is removed or a throw is confirmed."""
        return self._guard_present_die

    def require_motion(self) -> None:
        """Start the next throw only on clear motion (the crop has just changed)."""
        self._motion_required = True

    def reset_to_waiting(self) -> None:
        """Discard transient camera state before a new camera session starts."""
        self._last_confirmed_center = None
        self._motion_required = False
        self._clear_resting_die()
        self._last_observation_time = None
        self._cycle_started_time = None
        self._reset_cycle(RecognitionState.WAITING)

    def snapshot(self, *, error_code: str | None = None) -> RecognitionSnapshot:
        if error_code is None:
            if self.state is RecognitionState.CAMERA_ERROR:
                error_code = "camera_disconnected"
            elif self.state is RecognitionState.RECOGNITION_ERROR:
                error_code = self._recognition_error_code
        return RecognitionSnapshot(
            state=self.state,
            candidate_value=self._candidate_value,
            candidate_id=(
                self._candidate_id
                if self.state is RecognitionState.NEEDS_CONFIRMATION else None
            ),
            confidence=self._confidence,
            confirmation=self._confirmation,
            discarded_candidate=self._discarded_candidate,
            error_code=error_code,
            reading_id=(
                self._reading_id if self.state is RecognitionState.READING else None
            ),
            recognition=self._recognition,
        )

    def _observe_moving(self, frame: FrameObservation) -> None:
        motion_score = frame.motion_score
        if motion_score <= self.config.stationary_threshold:
            self._stationary_count += 1
            if self._stationary_count >= self.config.stationary_frames:
                if self._is_resting_die(frame):
                    self._back_to_resting_die()
                    return
                self._reset_cycle(RecognitionState.STATIONARY)
                if self.reading_mode == "vlm":
                    # The model reads the value, even when OpenCV sees no die
                    # (overexposure, an unusual background).
                    self._begin_reading()
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
        if self._is_resting_die(frame):
            self._back_to_resting_die()
            return
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
            candidate_value = recent[-1].observation.value
            if self._candidate_value != candidate_value or self._candidate_id is None:
                self._candidate_counter += 1
                self._candidate_id = (
                    f"{self.session_id}:candidate:{self._candidate_counter}"
                )
            self._candidate_value = candidate_value
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
        # Gate on the frame that completes the stable-value sequence. Earlier
        # transient frames may contain a partial pip while the die is settling;
        # using their minimum would demote otherwise clean confirmations.
        pip_size_consistency = matching[-1].observation.pip_size_consistency
        single_pip_center_offset = (
            matching[-1].observation.single_pip_center_offset
        )
        pip_merge_suspected = matching[-1].observation.pip_merge_suspected
        pip_recounted = matching[-1].observation.pip_recounted
        # Layout error is sensitive to a pip centre moving by a few pixels, and
        # a single live frame can spike (0.15 on a correct 5 whose neighbouring
        # frames read 0.10). Use the median over the stable-value run instead.
        # Side-face pips counted as top-face pips misfit in every frame, so
        # the median still catches them.
        pip_layout_error = median(
            sample.observation.pip_layout_error
            for sample in matching[-self.config.consecutive_value_frames:]
        )
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
            pip_size_consistency=pip_size_consistency,
            single_pip_center_offset=single_pip_center_offset,
            pip_merge_suspected=pip_merge_suspected,
            pip_recounted=pip_recounted,
            pip_layout_error=pip_layout_error,
        )
        automatic = (
            total >= self.config.automatic_threshold
            and pip_size_consistency >= self.config.min_pip_size_consistency
            # Fused pips that a lower-threshold recount could not separate.
            # A successful recount was right in all 9 labelled cases, so it
            # may auto-confirm; the layout check below still applies to it.
            and not (pip_merge_suspected and not pip_recounted)
            and pip_layout_error <= self.config.max_pip_layout_error
            # A die seen obliquely shows a side face; its outline is no longer
            # square and side-face pips can be counted as top-face pips
            # (4 read as 6, 3 read as 5 at shape confidence 0.72-0.77).
            and detector >= self.config.min_detector_confidence
            and (
                candidate != 1
                or single_pip_center_offset
                <= self.config.max_single_pip_center_offset
            )
        )
        if self.reading_mode == "hybrid" and (
            automatic or total >= self.config.confirmation_threshold
        ):
            # Ask the model before deciding; keep what OpenCV would have done.
            self._opencv_candidate = candidate
            self._opencv_automatic = automatic
            self._begin_reading()
            return
        if automatic:
            self._recognition = {
                "recognizer": "opencv", "readingId": None, "opencvCandidate": candidate,
                "vlm": None, "vlmError": None,
            }
            self._set_confirmation(
                candidate, ConfirmationSource.AUTOMATIC, monotonic_time,
                original_candidate=candidate,
            )
        elif total >= self.config.confirmation_threshold:
            self._recognition = {
                "recognizer": "opencv", "readingId": None, "opencvCandidate": candidate,
                "vlm": None, "vlmError": None,
            }
            self.state = RecognitionState.NEEDS_CONFIRMATION
        else:
            discarded = DiscardedCandidate(candidate, self._confidence, monotonic_time)
            self._reset_cycle(RecognitionState.STATIONARY)
            self._discarded_candidate = discarded

    def _observe_removal(self, frame: FrameObservation) -> None:
        if frame.motion_score >= self.config.movement_threshold:
            self._motion_required = False
            self._last_confirmed_center = None
            self._reset_cycle(RecognitionState.MOVING)
            self._cycle_started_time = frame.monotonic_time
            return
        if frame.detections or frame.rejected_count:
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
        self._clear_resting_die()
        if self._samples:
            latest = self._samples[-1]
            self._last_confirmed_center = latest.observation.center
            self._last_confirmed_roi_diagonal = latest.roi_diagonal
        else:
            self._last_confirmed_center = None
            if source is not ConfirmationSource.MANUAL:
                # Read by the model without an OpenCV position: only clear motion
                # may start the next throw, so the same die is never read twice.
                self._motion_required = True
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
            recognition=(
                None if source is ConfirmationSource.MANUAL else self._recognition
            ),
        )
        self.state = RecognitionState.CONFIRMED
        return self._confirmation

    def _clear_resting_die(self) -> None:
        self._guard_present_die = False
        self._resting_die = None
        self._resting_absent_since = None
        self._settle_frames_left = 0

    def _remember_resting_die(self, frame: FrameObservation) -> None:
        """Note where the die present at (re)start lies (one still die only)."""
        if (
            self._guard_present_die
            and len(frame.detections) == 1
            and frame.motion_score <= self.config.stationary_threshold
        ):
            die = frame.detections[0]
            self._resting_die = (die.center, die.value, frame.roi_diagonal)

    def _track_resting_die(self, frame: FrameObservation) -> None:
        """Forget the resting die once it has been out of sight for a while."""
        if self._resting_die is None:
            return
        if frame.detections or frame.rejected_count:
            self._resting_absent_since = None
            return
        if self._resting_absent_since is None:
            self._resting_absent_since = frame.monotonic_time
        elif (
            frame.monotonic_time - self._resting_absent_since
            >= self._RESTING_DIE_REMOVED_SECONDS
        ):
            self._clear_resting_die()

    def _is_resting_die(self, frame: FrameObservation) -> bool:
        """The single die in view is the one that was lying there at (re)start."""
        if self._resting_die is None or len(frame.detections) != 1:
            return False
        center, value, diagonal = self._resting_die
        die = frame.detections[0]
        limit = max(frame.roi_diagonal, diagonal) * self._DUPLICATE_POSITION_RATIO
        return die.value == value and dist(die.center, center) <= limit

    def _back_to_resting_die(self) -> None:
        """The motion was not a throw: wait again for the die to be picked up."""
        self._reset_cycle(RecognitionState.REMOVAL_WAIT)
        self._cycle_started_time = None
        self._motion_required = True

    def _die_is_away_from_last_confirmation(self, frame: FrameObservation) -> bool:
        previous = self._last_confirmed_center
        if previous is None:
            return True
        if not frame.detections:
            # A rejected outline has no center in FrameObservation. Without
            # motion, treating it as the confirmed die is the safe choice.
            return False
        diagonal = max(frame.roi_diagonal, self._last_confirmed_roi_diagonal)
        minimum_distance = diagonal * self._DUPLICATE_POSITION_RATIO
        return any(
            dist(detection.center, previous) > minimum_distance
            for detection in frame.detections
        )

    def _reset_cycle(self, state: RecognitionState) -> None:
        self._reset_transient()
        self.state = state
        if state is RecognitionState.WAITING:
            self._cycle_started_time = None

    def _reset_transient(self) -> None:
        self._stationary_count = 0
        self._absence_count = 0
        self._candidate_value = None
        self._candidate_id = None
        self._confidence = None
        self._confirmation = None
        self._discarded_candidate = None
        self._samples.clear()
        self._recognition_error_code = None
        self._die_seen_in_cycle = False
        self._reading_id = None
        self._opencv_candidate = None
        self._opencv_automatic = False
        self._recognition = None

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


def _reading_dict(reading: VisionReading) -> dict[str, Any]:
    return {
        "diceCount": reading.dice_count,
        "value": reading.value,
        "confidence": reading.confidence,
        "note": reading.note,
        "model": reading.model,
        "durationMs": reading.duration_ms,
    }
