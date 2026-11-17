import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from commentator.services.vision.motion import Frame
from commentator.services.vision.pipeline import RecognitionFrameResult
from commentator.services.vision.recognition import (
    RecognitionSnapshot,
    RecognitionState,
)
from commentator.services.vision.settings import VisionSettings, VisionSettingsStore
from commentator.tools.vision_bench_core import VisionBenchEngine, VisionBenchRecorder


class FakeWriter:
    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.released = False

    def isOpened(self) -> bool:
        return True

    def write(self, frame: np.ndarray) -> None:
        self.frames.append(frame.copy())

    def release(self) -> None:
        self.released = True


def _result(state: RecognitionState, *, motion_score: float = 0.0) -> RecognitionFrameResult:
    return RecognitionFrameResult(
        snapshot=RecognitionSnapshot(
            state=state,
            candidate_value=None,
            confidence=None,
            confirmation=None,
            discarded_candidate=None,
        ),
        motion_score=motion_score,
        detections=(),
        rejected_count=0,
        white_mask=np.zeros((20, 30), dtype=np.uint8),
    )


def test_recorder_writes_video_sidecars_events_and_evaluation_labels(tmp_path: Path) -> None:
    writer = FakeWriter()
    recorder = VisionBenchRecorder(
        tmp_path,
        writer_factory=lambda _path, _fps, _size: writer,
        now=lambda: datetime(2026, 11, 16, 9, 30, 0),
    )
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    paths = recorder.start(
        frame,
        monotonic_time=10.0,
        settings=VisionSettings(
            camera_id=0,
            white_threshold=220,
            pip_threshold=110,
        ),
    )

    recorder.record(frame, monotonic_time=10.0, result=_result(RecognitionState.WAITING))
    recorder.record(
        frame,
        monotonic_time=11.0,
        result=_result(RecognitionState.MOVING, motion_score=0.2),
    )
    recorder.label_current(4)
    assert recorder.rolls_started == 1
    assert recorder.labeled_rolls == 1
    assert recorder.rolls_without_label == 0
    recorder.record_decision(
        monotonic_time=12.0,
        kind="automatic",
        value=4,
        confidence=None,
    )
    recorder.record(frame, monotonic_time=12.0, result=_result(RecognitionState.CONFIRMED))
    recorder.record(frame, monotonic_time=13.0, result=_result(RecognitionState.WAITING))
    stopped = recorder.stop(monotonic_time=14.0)

    assert stopped == paths
    assert len(writer.frames) == 4
    assert writer.released
    frame_rows = [json.loads(line) for line in paths.frames.read_text().splitlines()]
    assert [row["seconds"] for row in frame_rows] == [0.0, 1.0, 2.0, 3.0]
    event_rows = [json.loads(line) for line in paths.events.read_text().splitlines()]
    state_rows = [row for row in event_rows if row["type"] == "state"]
    assert [row["state"] for row in state_rows] == [
        "waiting",
        "moving",
        "confirmed",
        "waiting",
    ]
    assert json.loads(paths.labels.read_text()) == {
        "rolls": [{
            "id": "roll-001",
            "expectedValue": 4,
            "startSeconds": 1.0,
            "endSeconds": 3.0,
        }]
    }
    settings = json.loads(paths.settings.read_text())
    assert settings["whiteThreshold"] == 220
    assert settings["pipThreshold"] == 110
    assert settings["cameraResolution"] == {"width": 1280, "height": 720}
    assert settings["automaticThreshold"] == 0.83
    assert settings["confirmationThreshold"] == 0.60
    assert settings["stationaryFrames"] == 10
    assert settings["consecutiveValueFrames"] == 3
    assert settings["minPipSizeConsistency"] == 0.50
    assert settings["maxSinglePipCenterOffset"] == 0.22
    assert settings["maxPipLayoutError"] == 0.15
    assert settings["minDetectorConfidence"] == 0.78
    assert recorder.automatic_confirmations == 1
    assert recorder.review_candidates == 0
    assert recorder.labeled_rolls == 1


def test_recorder_does_not_invent_label_for_unlabeled_roll(tmp_path: Path) -> None:
    writer = FakeWriter()
    recorder = VisionBenchRecorder(
        tmp_path,
        writer_factory=lambda _path, _fps, _size: writer,
        now=lambda: datetime(2026, 11, 16, 9, 31, 0),
    )
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    paths = recorder.start(frame, monotonic_time=1.0, settings=VisionSettings())
    recorder.record(frame, monotonic_time=2.0, result=_result(RecognitionState.MOVING))
    recorder.record(frame, monotonic_time=3.0, result=_result(RecognitionState.WAITING))
    recorder.stop(monotonic_time=4.0)

    assert json.loads(paths.labels.read_text()) == {"rolls": []}
    assert recorder.unlabeled_rolls == 1
    assert recorder.rolls_started == 1
    assert recorder.labeled_rolls == 0
    assert recorder.rolls_without_label == 1


def test_live_settings_rebuild_detector_with_selected_white_threshold(
    tmp_path: Path,
) -> None:
    engine = VisionBenchEngine(
        VisionSettingsStore(tmp_path / "vision-settings.json"),
        tmp_path / "recordings",
    )

    engine.update_settings(VisionSettings(white_threshold=173, pip_threshold=91))

    assert engine.detector.white_threshold == 173
    assert engine.detector.pip_threshold == 91
    assert engine.pipeline.detector is engine.detector


def test_recognition_settings_wait_for_safe_state_without_resetting_session(
    tmp_path: Path,
) -> None:
    engine = VisionBenchEngine(
        VisionSettingsStore(tmp_path / "vision-settings.json"),
        tmp_path / "recordings",
    )
    session = engine.session
    session.state = RecognitionState.MOVING
    updated = replace(
        engine.settings,
        automatic_threshold=0.92,
        confirmation_threshold=0.75,
        stationary_frames=4,
        consecutive_value_frames=2,
        min_pip_size_consistency=0.6,
        max_single_pip_center_offset=0.24,
        max_pip_layout_error=0.2,
        min_detector_confidence=0.8,
    )

    engine.update_settings(updated)

    assert engine.session is session
    assert engine.recognition_settings_pending
    assert engine.active_recognition_config.stationary_frames == 10

    session.state = RecognitionState.REMOVAL_WAIT
    engine.process_frame(np.zeros((20, 30, 3), dtype=np.uint8), monotonic_time=1.0)

    assert engine.session is session
    assert not engine.recognition_settings_pending
    assert engine.active_recognition_config.stationary_frames == 4
    assert engine.active_recognition_config.consecutive_value_frames == 2
    assert engine.active_recognition_config.automatic_threshold == 0.92
    assert engine.active_recognition_config.confirmation_threshold == 0.75
    assert engine.active_recognition_config.min_pip_size_consistency == 0.6
    assert engine.active_recognition_config.max_single_pip_center_offset == 0.24
    assert engine.active_recognition_config.max_pip_layout_error == 0.2
    assert engine.active_recognition_config.min_detector_confidence == 0.8


def test_engine_renders_pip_count_debug_panel(tmp_path: Path) -> None:
    image = cv2.imread(str(
        Path(__file__).parent / "fixtures" / "dice" / "demo_dice.png"
    ))
    assert image is not None
    engine = VisionBenchEngine(
        VisionSettingsStore(tmp_path / "vision-settings.json"),
        tmp_path / "recordings",
    )

    result = engine.process_frame(cast(Frame, image), monotonic_time=1.0)

    assert len(result.result.pip_debug) == 5
    assert result.pip_debug_preview is not None
    assert result.pip_debug_preview.shape[1] == 360
