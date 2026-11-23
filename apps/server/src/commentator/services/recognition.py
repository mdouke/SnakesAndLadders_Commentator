"""Background recognition orchestration without importing optional vision modules."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, RLock, Thread, current_thread
from time import monotonic, sleep
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
from .vision.vlm import (
    VLM_PROMPT_VERSION,
    DiceReader,
    VlmError,
    VlmSettingsStore,
    vlm_availability,
)


class Pipeline(Protocol):
    session: DiceRecognitionSession

    def process(
        self, frame: Any, *, monotonic_time: float, observe: bool = True
    ) -> Any: ...
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
        vision_settings: Any = None,
        settings_store: Any = None,
        dice_reader: DiceReader | None = None,
        vlm_settings: VlmSettingsStore | None = None,
        image_encoder: Callable[[Any], bytes] | None = None,
        image_dir: Path | None = None,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        if stop_join_timeout <= 0:
            raise ValueError("停止待機時間は正の値にしてください")
        self.game_service = game_service
        self.pipeline = pipeline
        self.camera_provider = camera_provider
        if settings is None and vision_settings is not None:
            settings = RecognitionRuntimeSettings(
                camera_id=vision_settings.camera_id,
                camera_width=vision_settings.camera_width,
                camera_height=vision_settings.camera_height,
                exposure=vision_settings.exposure,
            )
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
        self._last_frame: Any = None
        self._last_detections: tuple[Any, ...] = ()
        self._gate_enabled = False
        self._game_ready = True
        self._requested_gate: tuple[str | None, bool] | None = None
        self._applied_gate: tuple[str | None, bool] | None = None
        # Tunable settings (``VisionSettings``) and their file store. Typed as Any
        # so this module does not import the optional vision dependencies.
        self.vision_settings: Any = vision_settings
        self.settings_store: Any = settings_store
        self._pending_config: Any = None
        self._camera_configure_pending = False
        self._last_result: Any = None
        self._frame_times: deque[float] = deque(maxlen=30)
        # Recognition log (no images): what happened to each throw.
        self._log_tray_changes = 0
        self._waiting_die_since: float | None = None
        self._waiting_die_logged = False
        # Reading the value with the vision model (ADR 0006).
        self.dice_reader = dice_reader
        self.vlm_settings = vlm_settings or VlmSettingsStore()
        self.image_encoder = image_encoder
        self.image_dir = image_dir
        self.retry_delay_seconds = retry_delay_seconds
        self._pending_mode: str | None = self.vlm_settings.get().reading_mode
        self._submitted_reading: str | None = None
        self._readings: deque[dict[str, Any]] = deque(maxlen=30)
        self._reader_pool: ThreadPoolExecutor | None = None
        self._apply_pending_config_locked()

    @property
    def available(self) -> bool:
        return self.pipeline is not None and self.camera_provider is not None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def send_snapshot(self, callback: Subscriber) -> None:
        """Pass the current snapshot under the lock that orders notifications."""
        with self._lock:
            callback(self.snapshot())

    def preview_jpeg(self, view: str = "overlay") -> bytes:
        """Render the latest processed frame.

        ``overlay`` is the full frame with the ROI and dice, ``mask`` the white mask
        inside the ROI and ``pips`` the pip-counting view of every candidate.
        """
        with self._lock:
            frame = self._last_frame
            result = self._last_result
            detections = self._last_detections
            running = self._running
        if not running or frame is None:
            raise ApiError(
                409,
                "recognition_preview_unavailable",
                "認識が動作していないため、プレビューを表示できません",
            )
        roi = getattr(self.pipeline, "roi", None)
        rejected = tuple(getattr(result, "rejected_detections", ()))
        try:
            from .vision.preview import (
                render_mask_jpeg,
                render_pips_jpeg,
                render_preview_jpeg,
            )

            if view == "mask" and getattr(result, "white_mask", None) is not None:
                return render_mask_jpeg(
                    result.white_mask, detections=detections, rejected=rejected
                )
            if view == "pips":
                return render_pips_jpeg(tuple(getattr(result, "pip_debug", ())))
            region = getattr(self.pipeline, "region", None)
            crop_origin = None
            tray_points = None
            if callable(region):
                bounds, mask = region(frame)
                crop_origin = (bounds.x, bounds.y)
                tray = getattr(self.pipeline, "tray", None)
                if mask is not None and tray is not None and tray.shape is not None:
                    height, width = frame.shape[:2]
                    tray_points = tray.shape.pixels(width, height)
            return render_preview_jpeg(
                frame, roi=roi, detections=detections, rejected=rejected,
                crop_origin=crop_origin, tray=tray_points,
            )
        except Exception as error:
            logging.getLogger("commentator.recognition").exception(
                "recognition preview failed",
                extra={"event": "recognition_preview_failed"},
            )
            raise ApiError(
                503, "recognition_preview_failed", "プレビューを作成できません"
            ) from error

    # --- tunable settings -------------------------------------------------

    def _require_tunable(self) -> None:
        if not self.available or self.vision_settings is None:
            raise ApiError(
                503, "recognition_unavailable", "サイコロ認識を利用できません",
                {"reason": self.unavailable_reason},
            )

    def _saved_settings(self) -> Any:
        if self.settings_store is None:
            return self.vision_settings
        try:
            return self.settings_store.load()
        except ValueError:
            return None

    def vision_settings_payload(self) -> dict[str, Any]:
        self._require_tunable()
        with self._lock:
            current = self.vision_settings
            pending = self._pending_config is not None
        saved = self._saved_settings()
        return {
            "settings": current.to_dict(),
            "saved": saved.to_dict() if saved is not None else None,
            "defaults": type(current)().to_dict(),
            "unsaved": saved != current,
            "pendingRecognitionSettings": pending,
            "settingsPath": (
                str(self.settings_store.path) if self.settings_store is not None else None
            ),
        }

    def update_vision_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply a partial update to the running recognition without saving it."""
        self._require_tunable()
        try:
            updated = self.vision_settings.with_changes(changes)
        except ValueError as error:
            raise ApiError(422, "invalid_settings", str(error)) from error
        self._apply_vision_settings(updated)
        logging.getLogger("commentator.settings").info(
            "vision settings applied",
            extra={"event": "vision_settings_applied", "fields": sorted(changes)},
        )
        return self.vision_settings_payload()

    def save_vision_settings(self) -> dict[str, Any]:
        self._require_tunable()
        if self.settings_store is None:
            raise ApiError(409, "settings_not_persistent", "設定の保存先がありません")
        with self._lock:
            current = self.vision_settings
        try:
            self.settings_store.save(current)
        except OSError as error:
            raise ApiError(
                503, "settings_save_failed", "サイコロ認識の設定を保存できません"
            ) from error
        logging.getLogger("commentator.settings").info(
            "vision settings saved", extra={"event": "vision_settings_saved"}
        )
        return self.vision_settings_payload()

    def revert_vision_settings(self, *, defaults: bool = False) -> dict[str, Any]:
        """Go back to the saved file (or the built-in defaults) without saving."""
        self._require_tunable()
        target = type(self.vision_settings)() if defaults else self._saved_settings()
        if target is None:
            raise ApiError(
                409, "settings_unreadable", "保存されたサイコロ認識の設定を読み込めません"
            )
        self._apply_vision_settings(target)
        return self.vision_settings_payload()

    def _apply_vision_settings(self, updated: Any) -> None:
        restart_camera: int | None = None
        with self._lock:
            previous = self.vision_settings
            self.vision_settings = updated
            pipeline = self.pipeline
            if pipeline is not None:
                if getattr(pipeline, "roi", None) != updated.roi:
                    pipeline.roi = updated.roi  # type: ignore[attr-defined]
                    pipeline.reset_frames()  # the motion baseline belongs to the old ROI
                detector = getattr(pipeline, "detector", None)
                if detector is not None:
                    detector.white_threshold = updated.white_threshold
                    detector.pip_threshold = updated.pip_threshold
                set_tray = getattr(pipeline, "set_tray_detection", None)
                if callable(set_tray):
                    set_tray(updated.tray_detection)
                config = updated.recognition_config()
                if config != pipeline.session.config:
                    self._pending_config = config
                    self._apply_pending_config_locked()
            camera = (updated.camera_width, updated.camera_height, updated.exposure)
            previous_camera = (
                self.settings.camera_width, self.settings.camera_height,
                self.settings.exposure,
            )
            camera_changed = previous.camera_id != updated.camera_id
            self.settings = replace(
                self.settings,
                camera_id=updated.camera_id,
                camera_width=updated.camera_width,
                camera_height=updated.camera_height,
                exposure=updated.exposure,
            )
            if self._running:
                if camera_changed and updated.camera_id is not None:
                    restart_camera = updated.camera_id
                elif camera != previous_camera:
                    # The worker applies this before its next read, so the capture
                    # object is only touched from one thread.
                    self._camera_configure_pending = True
        if restart_camera is not None:
            self.stop()
            self.start(restart_camera)
        self._notify_if_changed()

    def _apply_pending_config_locked(self) -> None:
        """Change thresholds only between throws, never in the middle of one."""
        if self.pipeline is None or (
            self._pending_config is None and self._pending_mode is None
        ):
            return
        session = self.pipeline.session
        if session.state in {
            RecognitionState.WAITING,
            RecognitionState.REMOVAL_WAIT,
            RecognitionState.RECOGNITION_ERROR,
            RecognitionState.CAMERA_ERROR,
        }:
            if self._pending_config is not None:
                session.config = self._pending_config
                self._pending_config = None
            if self._pending_mode is not None:
                session.reading_mode = self._pending_mode  # type: ignore[assignment]
                self._pending_mode = None

    def _configure_camera_if_pending(self, stream: CameraStream) -> None:
        with self._lock:
            if not self._camera_configure_pending:
                return
            self._camera_configure_pending = False
            settings = self.settings
        configure = getattr(stream, "configure", None)
        if callable(configure):
            try:
                configure(
                    width=settings.camera_width,
                    height=settings.camera_height,
                    exposure=settings.exposure,
                )
            except Exception:
                logging.getLogger("commentator.recognition").exception(
                    "camera reconfiguration failed",
                    extra={"event": "camera_configure_failed",
                           "errorCode": "camera_configure_failed"},
                )
            if self.pipeline is not None:
                with self._lock:
                    self.pipeline.reset_frames()

    def diagnostics(self) -> dict[str, Any]:
        """Numbers and hints for tuning; computed from the newest frame only."""
        with self._lock:
            frame = self._last_frame
            result = self._last_result
            running = self._running
            settings = self.vision_settings
            pipeline = self.pipeline
            times = list(self._frame_times)
            pending = self._pending_config is not None
        payload: dict[str, Any] = {
            "running": running,
            "frameWidth": None,
            "frameHeight": None,
            "fps": None,
            "motionScore": None,
            "roiBrightness": None,
            "whiteThreshold": None,
            "whiteAreaRatio": None,
            "detections": [],
            "rejected": [],
            "pendingRecognitionSettings": pending,
            "tray": _tray_info(pipeline),
            "hints": [],
        }
        if not running or frame is None or result is None or pipeline is None:
            return payload
        import numpy as np

        height, width = frame.shape[:2]
        payload["frameWidth"] = int(width)
        payload["frameHeight"] = int(height)
        if len(times) >= 2 and times[-1] > times[0]:
            payload["fps"] = round((len(times) - 1) / (times[-1] - times[0]), 1)
        payload["motionScore"] = round(float(result.motion_score), 4)
        region = getattr(pipeline, "region", None)
        if callable(region):
            bounds, mask = region(frame)
            cropped = bounds.crop(frame)
        else:
            cropped = pipeline.roi.crop(frame) if hasattr(pipeline, "roi") else frame
            mask = None
        gray = cropped.mean(axis=2) if cropped.ndim == 3 else cropped
        if mask is not None:
            gray = gray[mask > 0]  # inside the tray only
        brightness = {
            "mean": round(float(gray.mean()), 1),
            "p99": round(float(np.percentile(gray, 99)), 1),
        }
        payload["roiBrightness"] = brightness
        detector = getattr(pipeline, "detector", None)
        applied = getattr(detector, "last_white_threshold", None)
        payload["whiteThreshold"] = {
            "mode": "auto" if settings is None or settings.white_threshold is None
            else "fixed",
            "applied": round(float(applied), 1) if applied is not None else None,
        }
        mask = getattr(result, "white_mask", None)
        if mask is not None and mask.size:
            payload["whiteAreaRatio"] = round(float((mask > 0).mean()), 3)
        payload["detections"] = [
            {
                "value": detection.value,
                "confidence": round(float(detection.confidence), 3),
                "pipSizeConsistency": round(float(detection.pip_size_consistency), 3),
                "pipLayoutError": round(float(detection.pip_layout_error), 3),
            }
            for detection in result.detections
        ]
        payload["rejected"] = [
            {"reason": item.reason}
            for item in getattr(result, "rejected_detections", ())
        ]
        payload["hints"] = _tuning_hints(payload, settings)
        return payload

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
            self._applied_gate = None  # re-arm: a die already present is ignored
            self._cycle_context = None
            self._cycle_context_captured = False
            self._last_frame = None
            self._last_detections = ()
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
            self._process_locked(frame)
        self._notify_if_changed()
        return self.snapshot()

    def attach_game(self) -> None:
        """Recognize dice only while the current game is in progress.

        Before this is called (unit tests of the bare service) every frame is
        recognized. Afterwards frames outside an in-progress game only feed the
        preview, and a die already lying in the ROI when play starts is ignored.
        """
        self._gate_enabled = True
        self.game_service.add_listener(self.game_changed)
        self.game_changed(self.game_service.current_payload())

    def game_changed(self, game: dict[str, Any] | None) -> None:
        """Game listener. Runs under the game lock, so it only records the new
        state; the recognition thread applies it under the recognition lock
        (lock order: recognition -> game)."""
        active = game is not None and game["status"] == "in_progress"
        self._requested_gate = (game["id"] if game is not None else None, active)

    def _process_locked(self, frame: Any) -> None:
        assert self.pipeline is not None
        self._apply_gate_locked()
        observe = self._game_ready
        before = self.pipeline.session.snapshot().state
        result = self.pipeline.process(
            frame, monotonic_time=self.clock(), observe=observe
        )
        self._remember_frame(frame, result)
        if observe:
            self._log_recognition_locked(before, result)
            self._capture_cycle_context(before, result.snapshot.state)
            self._handle_snapshot_locked(result.snapshot)
            self._start_reading_if_needed_locked(frame, result.snapshot)
        self._apply_pending_config_locked()

    def _log_recognition_locked(self, before: RecognitionState, result: Any) -> None:
        """Write what recognition decided, so a throw that ended up typed by hand
        can be traced from the log. Numbers and state names only, never images."""
        assert self.pipeline is not None
        snapshot: RecognitionSnapshot = result.snapshot
        session = self.pipeline.session
        tray_changes = getattr(self.pipeline, "tray_changes", 0)
        if tray_changes != self._log_tray_changes:
            self._log_tray_changes = tray_changes
            _recognition_log("tray outline changed", "recognition_tray_changed",
                             {"state": snapshot.state.value})
        detections = getattr(result, "detections", ())
        if snapshot.state is RecognitionState.WAITING and detections:
            now = self.clock()
            if self._waiting_die_since is None:
                self._waiting_die_since = now
            elif not self._waiting_die_logged and now - self._waiting_die_since >= 3.0:
                # A die has been lying there without starting a throw.
                self._waiting_die_logged = True
                _recognition_log("die visible while waiting", "recognition_die_ignored", {
                    "values": [getattr(item, "value", None) for item in detections],
                    "motionScore": round(float(result.motion_score), 4),
                    "motionRequired": bool(getattr(session, "motion_required", False)),
                })
        else:
            self._waiting_die_since = None
            self._waiting_die_logged = False
        discarded = snapshot.discarded_candidate
        if discarded is not None:
            _recognition_log("candidate discarded", "recognition_candidate_discarded", {
                "value": discarded.value,
                "confidence": _rounded(_confidence_dict(discarded.confidence)),
            })
        after = snapshot.state
        if after is before:
            return
        details: dict[str, Any] = {"from": before.value, "to": after.value}
        if after in {
            RecognitionState.CONFIRMED, RecognitionState.NEEDS_CONFIRMATION,
            RecognitionState.READING,
        }:
            details["candidate"] = snapshot.candidate_value
            if snapshot.confidence is not None:
                details["confidence"] = _rounded(_confidence_dict(snapshot.confidence))
            failed = getattr(session, "last_failed_checks", ())
            if failed:
                details["failedChecks"] = list(failed)
            if snapshot.recognition:
                details["recognizer"] = snapshot.recognition.get("recognizer")
        if snapshot.confirmation is not None:
            details["source"] = snapshot.confirmation.source.value
        if snapshot.error_code:
            details["errorCode"] = snapshot.error_code
        _recognition_log("recognition state changed", "recognition_state_changed", details)

    # --- vision model ------------------------------------------------------

    def vlm_settings_payload(self) -> dict[str, Any]:
        with self._lock:
            active = self.pipeline.session.reading_mode if self.pipeline else None
            pending = self._pending_mode is not None
        return {
            **self.vlm_settings.get().to_dict(),
            **vlm_availability(),
            "readerConfigured": self.dice_reader is not None,
            "activeReadingMode": active,
            "pendingReadingMode": pending,
            "promptVersion": VLM_PROMPT_VERSION,
        }

    def update_vlm_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = self.vlm_settings.update(changes)
        except (ValueError, TypeError) as error:
            raise ApiError(422, "invalid_settings", str(error)) from error
        with self._lock:
            # A mode change waits for the current throw, like the decision thresholds.
            self._pending_mode = settings.reading_mode
            self._apply_pending_config_locked()
        logging.getLogger("commentator.settings").info(
            "vlm settings changed",
            extra={"event": "vlm_settings_changed", "fields": sorted(changes)},
        )
        self._notify_if_changed()
        return self.vlm_settings_payload()

    def recent_readings(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._readings)

    def test_reading(self) -> dict[str, Any]:
        """Read the newest frame now; the result is shown only, never registered."""
        with self._lock:
            frame = self._last_frame
            running = self._running
        if not running or frame is None:
            raise ApiError(409, "recognition_preview_unavailable",
                           "認識を開始してから試してください")
        settings = self.vlm_settings.get()
        reading, error, attempts = self._call_reader(self._crop(frame), settings, None)
        record = self._record_reading(
            reading_id=None, reading=reading, error=error, attempts=attempts,
            status="test" if reading is not None else "failed", mode="test",
            model=settings.model,
        )
        if reading is None:
            raise ApiError(503, f"vlm_{error}", "画像認識で読み取れませんでした",
                           {"reading": record})
        return record

    def _crop(self, frame: Any) -> Any:
        crop = getattr(self.pipeline, "crop", None)
        if callable(crop):
            cropped = crop(frame)  # the tray outline when it is detected
        else:
            roi = getattr(self.pipeline, "roi", None)
            cropped = roi.crop(frame) if roi is not None else frame
        copy = getattr(cropped, "copy", None)
        return copy() if callable(copy) else cropped

    def _start_reading_if_needed_locked(self, frame: Any, snapshot: RecognitionSnapshot) -> None:
        reading_id = snapshot.reading_id
        if (
            snapshot.state is not RecognitionState.READING
            or reading_id is None
            or reading_id == self._submitted_reading
        ):
            return
        self._submitted_reading = reading_id
        if self._reader_pool is None:
            self._reader_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vlm")
        self._reader_pool.submit(
            self._read_and_apply, reading_id, self._crop(frame), self.vlm_settings.get()
        )

    def _reading_is_current(self, reading_id: str | None) -> bool:
        if reading_id is None:
            return True
        with self._lock:
            return (
                self.pipeline is not None
                and self.pipeline.session.snapshot().reading_id == reading_id
            )

    def _call_reader(
        self, image: Any, settings: Any, reading_id: str | None
    ) -> tuple[Any, str | None, int]:
        """Encode and read with retries; returns (reading, error code, attempts)."""
        if self.dice_reader is None:
            return None, "unavailable", 0
        try:
            encode = self.image_encoder
            if encode is None:
                from .vision.vlm import encode_jpeg

                encode = encode_jpeg
            jpeg = encode(image)
        except Exception:
            logging.getLogger("commentator.recognition").exception(
                "vlm image encoding failed", extra={"event": "vlm_encode_failed"}
            )
            return None, "encode_failed", 0
        if settings.save_images and self.image_dir is not None:
            self._save_image(jpeg, reading_id)
        error: str | None = None
        attempts = 0
        for attempt in range(settings.max_retries + 1):
            if not self._reading_is_current(reading_id):
                return None, "stale", attempts
            attempts += 1
            try:
                return (
                    self.dice_reader.read(
                        jpeg, model=settings.model, timeout=settings.timeout_seconds
                    ),
                    None,
                    attempts,
                )
            except VlmError as failure:
                error = failure.code
                if not failure.retryable or attempt == settings.max_retries:
                    break
                sleep(self.retry_delay_seconds * 2**attempt)
            except Exception:
                logging.getLogger("commentator.recognition").exception(
                    "vlm reader failed", extra={"event": "vlm_reader_failed"}
                )
                error = "error"
                break
        return None, error, attempts

    def _read_and_apply(self, reading_id: str, image: Any, settings: Any) -> None:
        try:
            reading, error, attempts = self._call_reader(image, settings, reading_id)
            with self._lock:
                session = self.pipeline.session if self.pipeline is not None else None
                accepted = session is not None and error != "stale" and session.complete_reading(
                    reading_id, reading, monotonic_time=self.clock(), error_code=error
                )
                status = (
                    "used" if accepted and reading is not None
                    else "failed" if accepted
                    else "discarded"
                )
                self._record_reading(
                    reading_id=reading_id, reading=reading, error=error, attempts=attempts,
                    status=status, mode=settings.reading_mode, model=settings.model,
                )
                if accepted and session is not None:
                    self._handle_snapshot_locked(session.snapshot())
            self._notify_if_changed()
        except Exception:
            logging.getLogger("commentator.recognition").exception(
                "vlm reading failed", extra={"event": "vlm_reading_failed"}
            )

    def _record_reading(
        self, *, reading_id: str | None, reading: Any, error: str | None, attempts: int,
        status: str, mode: str, model: str,
    ) -> dict[str, Any]:
        record = {
            "readingId": reading_id,
            "at": datetime.now(UTC).isoformat(),
            "mode": mode,
            "status": status,
            "model": reading.model if reading is not None else model,
            "promptVersion": VLM_PROMPT_VERSION,
            "attempts": attempts,
            "result": (
                {
                    "diceCount": reading.dice_count,
                    "value": reading.value,
                    "confidence": reading.confidence,
                    "note": reading.note,
                }
                if reading is not None else None
            ),
            "error": error,
            "durationMs": reading.duration_ms if reading is not None else None,
        }
        with self._lock:
            self._readings.appendleft(record)
        logging.getLogger("commentator.recognition").info(
            "vlm reading", extra={"event": "vlm_reading", **{
                key: value for key, value in record.items() if key != "at"
            }},
        )
        return record

    def _save_image(self, jpeg: bytes, reading_id: str | None) -> None:
        assert self.image_dir is not None
        try:
            now = datetime.now(UTC)
            folder = self.image_dir / now.strftime("%Y%m%d")
            folder.mkdir(parents=True, exist_ok=True)
            name = (reading_id or "test").replace(":", "_")
            (folder / f"{now.strftime('%H%M%S%f')}-{name}.jpg").write_bytes(jpeg)
        except OSError:
            logging.getLogger("commentator.recognition").exception(
                "vlm image save failed", extra={"event": "vlm_image_save_failed"}
            )

    def _apply_gate_locked(self) -> None:
        if not self._gate_enabled or self.pipeline is None:
            return
        requested = self._requested_gate
        if requested == self._applied_gate:
            return
        self._applied_gate = requested
        session = self.pipeline.session
        self._cycle_context = None
        self._cycle_context_captured = False
        if requested is not None and requested[1]:
            # Play (re)started: ignore a die that is already on the board.
            session.ignore_present_dice()
            self._game_ready = True
        else:
            session.reset_to_waiting()
            self._game_ready = False
        self._error_code = None

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
                "gameReady": self._game_ready,
                "state": (
                    recognition.state.value if recognition else "unavailable"
                ),
                "candidateValue": recognition.candidate_value if recognition else None,
                "candidateId": recognition.candidate_id if recognition else None,
                "readingMode": (
                    self.pipeline.session.reading_mode if self.pipeline else "opencv"
                ),
                "readingId": recognition.reading_id if recognition else None,
                "recognition": recognition.recognition if recognition else None,
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
                self._configure_camera_if_pending(stream)
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
                        self._process_locked(frame)
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
                self._last_frame = None
                self._last_result = None
                self._last_detections = ()
                self._frame_times.clear()
            if stream is not None:
                stream.close()
            self._notify_if_changed()

    def _remember_frame(self, frame: Any, result: Any) -> None:
        # Keep only a reference to the newest frame for the preview endpoint.
        # Frames are never logged or written to disk.
        self._last_frame = frame
        self._last_result = result
        self._last_detections = tuple(getattr(result, "detections", ()))
        self._frame_times.append(self.clock())

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
        # Subscribers run under the recognition lock so that notifications from the
        # worker and HTTP threads keep their order. They must not block.
        with self._lock:
            current = self.snapshot()
            if current == self._last_notified:
                return
            self._last_notified = current
            for subscriber in tuple(self._subscribers):
                try:
                    subscriber(current)
                except Exception:
                    logging.getLogger("commentator.recognition").exception(
                        "recognition subscriber failed",
                        extra={"event": "recognition_subscriber_failed"},
                    )


def _tray_info(pipeline: Any) -> dict[str, Any]:
    tray = getattr(pipeline, "tray", None)
    enabled = bool(getattr(pipeline, "tray_detection", False))
    shape = tray.shape if tray is not None and enabled else None
    updated = tray.updated_at if tray is not None else None
    return {
        "enabled": enabled,
        "found": shape is not None,
        "lastAttemptFound": tray.last_attempt_found if tray is not None and enabled else None,
        "vertices": len(shape.points) if shape is not None else None,
        "areaRatio": shape.area_ratio if shape is not None else None,
        "secondsSinceUpdate": (
            round(monotonic() - updated, 1) if shape is not None and updated is not None
            else None
        ),
    }


def _tuning_hints(payload: dict[str, Any], settings: Any) -> list[str]:
    """Plain-language advice for the most common failures in a new environment."""
    hints: list[str] = []
    width, height = payload["frameWidth"], payload["frameHeight"]
    if settings is not None and (width, height) != (
        settings.camera_width, settings.camera_height
    ):
        hints.append(
            f"カメラは {width}x{height} で映像を返しています"
            f"（設定は {settings.camera_width}x{settings.camera_height}）。"
            "このカメラが対応していない解像度の可能性があります。"
        )
    brightness = payload["roiBrightness"]
    ratio = payload["whiteAreaRatio"]
    detections, rejected = payload["detections"], payload["rejected"]
    if not detections and not rejected:
        fixed = settings is not None and settings.white_threshold is not None
        if fixed and brightness["p99"] < settings.white_threshold:
            suggestion = max(0, int(brightness["p99"]) - 20)
            hints.append(
                f"枠の中で一番明るい部分（{brightness['p99']:.0f}）が外形しきい値"
                f"（{settings.white_threshold}）より暗いので、サイコロが見えていません。"
                f"外形しきい値を {suggestion} 前後まで下げるか「自動」にしてください。"
            )
        elif ratio is not None and ratio > 0.4:
            hints.append(
                f"枠の中の {ratio:.0%} が白として扱われています。背景まで白く見えているので、"
                "外形しきい値を上げるか、枠をトレーの内側だけにしてください。"
            )
        elif brightness["mean"] < 20:
            hints.append(
                "映像がとても暗いです。照明を明るくするか、露出を上げてください。"
            )
        else:
            hints.append(
                "サイコロが見つかりません。枠（緑）の中にサイコロがあるか確認し、"
                "「外形マスク」でサイコロが白い四角として見えるように外形しきい値を調整してください。"
            )
    elif rejected and not detections:
        pip = settings.pip_threshold if settings is not None else None
        if pip is not None and pip >= brightness["p99"]:
            suggestion = max(0, int(brightness["p99"]) - 60)
            hints.append(
                f"サイコロらしい形はありますが、目しきい値（{pip}）がサイコロの面の明るさ"
                f"（{brightness['p99']:.0f}）以上なので、面全体が目のように見えています。"
                f"目しきい値を {suggestion} 前後まで下げるか「自動」にしてください。"
            )
        else:
            hints.append(
                "サイコロらしい形はありますが、目の数を読めていません。"
                "「目の計数」で目が緑の輪郭で囲まれているか確認し、目しきい値を調整してください。"
            )
    if len(detections) > 1:
        hints.append(
            "サイコロが2個以上見えています。枠の中にサイコロ以外の白い物がないか確認してください。"
        )
    tray = payload["tray"]
    if tray["enabled"] and not tray["found"]:
        hints.insert(0,
            "トレーを検出できていません。緑の枠をトレー全体が入る大きさにし、"
            "トレーの上から手やサイコロ以外の物をどけてください（投擲待ちで動きがない時に探します）。"
        )
    elif tray["enabled"] and tray["lastAttemptFound"] is False:
        hints.append("最新の映像ではトレーが見つかりませんでした。前に見つけた形を使っています。")
    if payload["pendingRecognitionSettings"]:
        hints.append("判定の設定は、今の投擲が終わってから反映されます。")
    return hints


def _recognition_log(message: str, event: str, details: dict[str, Any]) -> None:
    logging.getLogger("commentator.recognition").info(
        message, extra={"event": event, "recognition": details}
    )


def _rounded(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in values.items()
    }


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
        "recognition": value.recognition,
        "confidence": _confidence_dict(value.confidence) if value.confidence else None,
    }
