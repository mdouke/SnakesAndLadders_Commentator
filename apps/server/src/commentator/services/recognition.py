"""Background recognition orchestration without importing optional vision modules."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, RLock, Thread, current_thread
from time import monotonic
from typing import Any, Protocol

from ..api.errors import ApiError
from .game import GameService
from .vision.recognition import (
    ConfidenceBreakdown,
    Confirmation,
    ConfirmationSource,
    DiceRecognitionSession,
    RecognitionSnapshot,
    RecognitionState,
)


class Pipeline(Protocol):
    session: DiceRecognitionSession

    def process(self, frame: Any, *, monotonic_time: float) -> Any: ...
    def camera_disconnected(self, *, monotonic_time: float) -> RecognitionSnapshot: ...
    def reset_frames(self) -> None: ...


class CameraStream(Protocol):
    def read(self) -> Any: ...
    def close(self) -> None: ...


class CameraProvider(Protocol):
    def enumerate(self) -> list[Any]: ...
    def open(self, device_id: int) -> CameraStream: ...


@dataclass(frozen=True, slots=True)
class RecognitionRuntimeSettings:
    camera_id: int | None = None
    camera_width: int = 1280
    camera_height: int = 720
    exposure: float | None = None


Subscriber = Callable[[dict[str, Any]], None]


class RecognitionService:
    """Own a pipeline, camera worker and game-registration boundary."""

    def __init__(
        self,
        game_service: GameService,
        *,
        pipeline: Pipeline | None,
        camera_provider: CameraProvider | None,
        settings: RecognitionRuntimeSettings | None = None,
        clock: Callable[[], float] = monotonic,
        unavailable_reason: str | None = None,
        camera_disconnected_errors: tuple[type[BaseException], ...] = (),
        stop_join_timeout: float = 2.0,
    ) -> None:
        if stop_join_timeout <= 0:
            raise ValueError("停止待機時間は正の値にしてください")
        self.game_service = game_service
        self.pipeline = pipeline
        self.camera_provider = camera_provider
        self.settings = settings or RecognitionRuntimeSettings()
        self.clock = clock
        self.unavailable_reason = unavailable_reason
        self.camera_disconnected_errors = camera_disconnected_errors
        self.stop_join_timeout = stop_join_timeout
        self._lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._stream: CameraStream | None = None
        self._running = False
        self._camera_connected = False
        self._error_code = "recognition_unavailable" if not self.available else None
        self._last_confirmation: dict[str, Any] | None = None
        self._handled_confirmation_ids: deque[str] = deque(maxlen=256)
        self._cycle_context: tuple[str, int, int] | None = None
        self._cycle_context_captured = False
        self._subscribers: set[Subscriber] = set()
        self._last_notified: dict[str, Any] | None = None

    @property
    def available(self) -> bool:
        return self.pipeline is not None and self.camera_provider is not None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.add(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.discard(callback)

        return unsubscribe

    def cameras(self) -> list[dict[str, Any]]:
        if not self.available or self.camera_provider is None:
            return []
        try:
            return [
                {"id": device.id, "label": device.label}
                for device in self.camera_provider.enumerate()
            ]
        except Exception as error:
            raise ApiError(
                503, "camera_enumeration_failed", "カメラの一覧を取得できません"
            ) from error

    def start(self, device_id: int | None = None) -> dict[str, Any]:
        if not self.available or self.camera_provider is None or self.pipeline is None:
            raise ApiError(
                503,
                "recognition_unavailable",
                "サイコロ認識を利用できません",
                {"reason": self.unavailable_reason},
            )
        selected = device_id if device_id is not None else self.settings.camera_id
        if selected is None:
            raise ApiError(409, "camera_not_selected", "カメラを選択してください")
        with self._lock:
            if self._running:
                raise ApiError(409, "recognition_already_running", "認識は動作中です")
            try:
                stream = self.camera_provider.open(selected)
            except Exception as error:
                raise ApiError(
                    503,
                    "camera_connection_failed",
                    "カメラに接続できません",
                    {"cameraId": selected},
                ) from error
            try:
                configure = getattr(stream, "configure", None)
                if callable(configure):
                    configure(
                        width=self.settings.camera_width,
                        height=self.settings.camera_height,
                        exposure=self.settings.exposure,
                    )
            except Exception as error:
                stream.close()
                raise ApiError(
                    503,
                    "camera_connection_failed",
                    "カメラを設定できません",
                    {"cameraId": selected},
                ) from error
            self.pipeline.session.reset_to_waiting()
            self.pipeline.reset_frames()
            self._cycle_context = None
            self._cycle_context_captured = False
            self._stream = stream
            self._stop.clear()
            self._running = True
            self._camera_connected = True
            self._error_code = None
            self._thread = Thread(
                target=self._run,
                name="dice-recognition",
                daemon=True,
            )
            self._thread.start()
        self._notify_if_changed()
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=self.stop_join_timeout)
            if thread.is_alive():
                with self._lock:
                    self._error_code = "recognition_stop_timeout"
                self._notify_if_changed()
                raise ApiError(
                    503,
                    "recognition_stop_timeout",
                    "認識スレッドを停止できませんでした",
                )
        with self._lock:
            if thread is None:
                self._running = False
                self._camera_connected = False
        self._notify_if_changed()
        return self.snapshot()

    def confirm_candidate(
        self,
        game_id: str,
        candidate_id: str,
        *,
        expected_state_version: int,
        expected_turn_number: int,
        value: int | None = None,
    ) -> dict[str, Any]:
        session = self._session_or_503()
        with self._lock:
            self._require_candidate(session.snapshot(), candidate_id)
            try:
                confirmation = session.confirm_candidate(
                    value=value, monotonic_time=self.clock()
                )
            except ValueError as error:
                raise ApiError(409, "candidate_not_pending", str(error)) from error
            failure: ApiError | None
            try:
                game = self.game_service.register_recognition_roll(
                    confirmation,
                    expected_game_id=game_id,
                    expected_state_version=expected_state_version,
                    expected_turn_number=expected_turn_number,
                )
            except ApiError as error:
                self._safe_discard_confirmation(session, confirmation.id)
                self._error_code = error.code
                failure = error
            else:
                failure = None
                acknowledged = self._safe_acknowledge(session, confirmation.id)
                self._remember_confirmation(confirmation)
                if acknowledged:
                    self._error_code = None
        if failure is not None:
            self._notify_if_changed()
            raise failure
        self._notify_if_changed()
        return game.to_dict()

    def reject_candidate(self, candidate_id: str) -> dict[str, Any]:
        session = self._session_or_503()
        with self._lock:
            self._require_candidate(session.snapshot(), candidate_id)
            session.reject_candidate()
            self._error_code = "candidate_rejected"
        self._notify_if_changed()
        return self.snapshot()

    def manual_committed(self, value: int) -> None:
        """Supersede transient recognition state after a manual game commit."""
        if not self.running or self.pipeline is None:
            return
        with self._lock:
            session = self.pipeline.session
            try:
                confirmation = session.enter_manual(value, monotonic_time=self.clock())
            except ValueError:
                # The game write already succeeded. A pending/confirmed/removal state
                # must not be allowed to produce another turn for the same physical die.
                session.reset_to_waiting()
                confirmation = session.enter_manual(value, monotonic_time=self.clock())
            acknowledged = self._safe_acknowledge(session, confirmation.id)
            self._remember_confirmation(confirmation)
            if acknowledged:
                self._error_code = None
            self._cycle_context = None
            self._cycle_context_captured = False
        self._notify_if_changed()

    def process_frame(self, frame: Any) -> dict[str, Any]:
        """Process an injected frame; useful for deterministic service tests."""
        if self.pipeline is None:
            raise ApiError(503, "recognition_unavailable", "サイコロ認識を利用できません")
        with self._lock:
            before = self.pipeline.session.snapshot().state
            result = self.pipeline.process(frame, monotonic_time=self.clock())
            self._capture_cycle_context(before, result.snapshot.state)
            self._handle_snapshot_locked(result.snapshot)
        self._notify_if_changed()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self.pipeline is None:
                recognition = None
            else:
                recognition = self.pipeline.session.snapshot()
            confidence = (
                _confidence_dict(recognition.confidence)
                if recognition and recognition.confidence else None
            )
            confirmation = (
                _confirmation_dict(recognition.confirmation)
                if recognition and recognition.confirmation else None
            )
            return {
                "available": self.available,
                "running": self._running,
                "cameraConnected": self._camera_connected,
                "state": (
                    recognition.state.value if recognition else "unavailable"
                ),
                "candidateValue": recognition.candidate_value if recognition else None,
                "candidateId": recognition.candidate_id if recognition else None,
                "confidence": confidence,
                "confirmation": confirmation,
                "lastConfirmation": self._last_confirmation,
                "errorCode": self._error_code or (
                    recognition.error_code if recognition else "recognition_unavailable"
                ),
            }

    def _run(self) -> None:
        assert self.pipeline is not None
        try:
            while not self._stop.is_set():
                with self._lock:
                    stream = self._stream
                if stream is None:
                    break
                try:
                    frame = stream.read()
                except self.camera_disconnected_errors as error:
                    if not self._stop.is_set():
                        self._record_worker_failure(
                            error, error_code="camera_disconnected", camera_error=True
                        )
                    break
                except Exception as error:
                    if not self._stop.is_set():
                        self._record_worker_failure(
                            error, error_code="recognition_worker_failed"
                        )
                    break
                if self._stop.is_set():
                    break
                try:
                    with self._lock:
                        before = self.pipeline.session.snapshot().state
                        result = self.pipeline.process(
                            frame, monotonic_time=self.clock()
                        )
                        self._capture_cycle_context(before, result.snapshot.state)
                        self._handle_snapshot_locked(result.snapshot)
                except Exception as error:
                    self._record_worker_failure(
                        error, error_code="recognition_worker_failed"
                    )
                    break
                self._notify_if_changed()
        finally:
            with self._lock:
                stream = self._stream
                self._stream = None
                self._running = False
                self._camera_connected = False
                self._thread = None
            if stream is not None:
                stream.close()
            self._notify_if_changed()

    def _handle_snapshot_locked(self, snapshot: RecognitionSnapshot) -> None:
        confirmation = snapshot.confirmation
        if (
            snapshot.state is RecognitionState.CONFIRMED
            and confirmation is not None
            and confirmation.source is ConfirmationSource.AUTOMATIC
            and confirmation.id not in self._handled_confirmation_ids
        ):
            self._mark_handled(confirmation.id)
            context = self._cycle_context if self._cycle_context_captured else None
            try:
                if context is None:
                    raise ApiError(
                        409,
                        "game_not_in_progress",
                        "投擲開始時に進行中のゲームがありません",
                    )
                self.game_service.register_recognition_roll(
                    confirmation,
                    expected_game_id=context[0],
                    expected_state_version=context[1],
                    expected_turn_number=context[2],
                )
            except ApiError as error:
                assert self.pipeline is not None
                self._safe_discard_confirmation(
                    self.pipeline.session, confirmation.id
                )
                self._error_code = error.code
            else:
                assert self.pipeline is not None
                acknowledged = self._safe_acknowledge(
                    self.pipeline.session, confirmation.id
                )
                self._remember_confirmation(confirmation)
                if acknowledged:
                    self._error_code = None

    def _capture_cycle_context(
        self, before: RecognitionState, after: RecognitionState
    ) -> None:
        if (
            after is RecognitionState.MOVING
            and before
            in {
                RecognitionState.WAITING,
                RecognitionState.REMOVAL_WAIT,
                RecognitionState.RECOGNITION_ERROR,
                RecognitionState.CAMERA_ERROR,
            }
        ):
            self._cycle_context = self.game_service.current_roll_context()
            self._cycle_context_captured = True

    def _record_worker_failure(
        self,
        error: BaseException,
        *,
        error_code: str,
        camera_error: bool = False,
    ) -> None:
        logging.getLogger("commentator.recognition").error(
            "recognition worker failed",
            exc_info=(type(error), error, error.__traceback__),
            extra={
                "event": "recognition_worker_failed",
                "errorCode": error_code,
            },
        )
        with self._lock:
            if camera_error and self.pipeline is not None:
                try:
                    self.pipeline.camera_disconnected(monotonic_time=self.clock())
                except Exception:
                    logging.getLogger("commentator.recognition").exception(
                        "recognition camera error transition failed",
                        extra={"event": "recognition_camera_transition_failed"},
                    )
            self._error_code = error_code

    def _session_or_503(self) -> DiceRecognitionSession:
        if self.pipeline is None:
            raise ApiError(503, "recognition_unavailable", "サイコロ認識を利用できません")
        return self.pipeline.session

    @staticmethod
    def _require_candidate(snapshot: RecognitionSnapshot, candidate_id: str) -> None:
        if (
            snapshot.state is not RecognitionState.NEEDS_CONFIRMATION
            or snapshot.candidate_id is None
        ):
            raise ApiError(409, "candidate_not_pending", "確認待ちの候補がありません")
        if snapshot.candidate_id != candidate_id:
            raise ApiError(
                409,
                "stale_candidate",
                "表示中の候補が更新されています",
                {"candidateId": snapshot.candidate_id},
            )

    def _remember_confirmation(self, confirmation: Confirmation) -> None:
        self._last_confirmation = _confirmation_dict(confirmation)
        self._mark_handled(confirmation.id)

    def _mark_handled(self, confirmation_id: str) -> None:
        if confirmation_id not in self._handled_confirmation_ids:
            self._handled_confirmation_ids.append(confirmation_id)

    def _safe_acknowledge(
        self, session: DiceRecognitionSession, confirmation_id: str
    ) -> bool:
        try:
            session.acknowledge_committed(confirmation_id)
        except ValueError:
            self._error_code = "recognition_state_conflict"
            logging.getLogger("commentator.recognition").exception(
                "recognition acknowledgement state conflict",
                extra={
                    "event": "recognition_state_conflict",
                    "errorCode": "recognition_state_conflict",
                    "confirmationId": confirmation_id,
                },
            )
            return False
        return True

    def _safe_discard_confirmation(
        self, session: DiceRecognitionSession, confirmation_id: str
    ) -> None:
        try:
            session.discard_confirmation(confirmation_id)
        except ValueError:
            self._error_code = "recognition_state_conflict"
            logging.getLogger("commentator.recognition").exception(
                "recognition discard state conflict",
                extra={
                    "event": "recognition_state_conflict",
                    "errorCode": "recognition_state_conflict",
                    "confirmationId": confirmation_id,
                },
            )

    def _notify_if_changed(self) -> None:
        current = self.snapshot()
        with self._lock:
            if current == self._last_notified:
                return
            self._last_notified = current
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(current)
            except Exception:
                logging.getLogger("commentator.recognition").exception(
                    "recognition subscriber failed",
                    extra={"event": "recognition_subscriber_failed"},
                )


def _confidence_dict(value: ConfidenceBreakdown) -> dict[str, Any]:
    return {
        "detector": value.detector,
        "valueStability": value.value_stability,
        "stillness": value.stillness,
        "positionStability": value.position_stability,
        "total": value.total,
        "pipSizeConsistency": value.pip_size_consistency,
        "singlePipCenterOffset": value.single_pip_center_offset,
        "pipMergeSuspected": value.pip_merge_suspected,
        "pipRecounted": value.pip_recounted,
        "pipLayoutError": value.pip_layout_error,
    }


def _confirmation_dict(value: Confirmation) -> dict[str, Any]:
    return {
        "confirmationId": value.id,
        "value": value.value,
        "source": value.source.value,
        "originalCandidate": value.original_candidate,
        "confidence": _confidence_dict(value.confidence) if value.confidence else None,
    }
