from pathlib import Path

import numpy as np
import pytest
from commentator.services.vision.camera import (
    CameraConnectionError,
    CameraDisconnectedError,
    OpenCVCameraProvider,
)
from commentator.services.vision.settings import (
    NormalizedRoi,
    VisionSettings,
    VisionSettingsStore,
)


class FakeCapture:
    def __init__(self, opened: bool, frames: list[np.ndarray] | None = None) -> None:
        self.opened = opened
        self.frames = list(frames or [])
        self.released = False

    def isOpened(self) -> bool:
        return self.opened and not self.released

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def release(self) -> None:
        self.released = True


def test_camera_enumeration_releases_every_probe() -> None:
    captures: list[FakeCapture] = []

    def factory(device_id: int) -> FakeCapture:
        capture = FakeCapture(opened=device_id in {0, 2})
        captures.append(capture)
        return capture

    provider = OpenCVCameraProvider(factory, enumeration_limit=4)
    assert [device.id for device in provider.enumerate()] == [0, 2]
    assert all(capture.released for capture in captures)


def test_camera_stream_detects_disconnect_and_closes() -> None:
    image = np.zeros((10, 20, 3), dtype=np.uint8)
    capture = FakeCapture(True, [image])
    provider = OpenCVCameraProvider(lambda _: capture)
    stream = provider.open(0)
    assert stream.read() is image
    with pytest.raises(CameraDisconnectedError, match="取得できません"):
        stream.read()
    stream.close()
    assert capture.released


def test_camera_open_failure_is_clear() -> None:
    provider = OpenCVCameraProvider(lambda _: FakeCapture(False))
    with pytest.raises(CameraConnectionError, match="接続できません"):
        provider.open(0)


def test_roi_crop_and_settings_round_trip(tmp_path: Path) -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    roi = NormalizedRoi(x=0.25, y=0.20, width=0.50, height=0.60)
    assert roi.crop(frame).shape == (60, 100, 3)

    store = VisionSettingsStore(tmp_path / "vision.json")
    assert store.load() == VisionSettings()
    settings = VisionSettings(
        camera_id=2,
        roi=roi,
        white_threshold=200,
        pip_threshold=105,
        camera_width=1920,
        camera_height=1080,
        exposure=-6.0,
        automatic_threshold=0.91,
        confirmation_threshold=0.72,
        stationary_frames=4,
        consecutive_value_frames=2,
        min_pip_size_consistency=0.55,
        max_single_pip_center_offset=0.24,
        max_pip_layout_error=0.2,
    )
    store.save(settings)
    assert store.load() == settings


@pytest.mark.parametrize("roi", [
    NormalizedRoi(x=0.9, width=0.1),
    NormalizedRoi(y=0.9, height=0.1),
])
def test_edge_aligned_roi_is_valid(roi: NormalizedRoi) -> None:
    assert roi.to_pixels(np.zeros((10, 10), dtype=np.uint8)).width >= 1


def test_invalid_settings_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "vision.json"
    path.write_text('{"cameraId": -1}', encoding="utf-8")
    with pytest.raises(ValueError, match="値が不正"):
        VisionSettingsStore(path).load()


def test_missing_thresholds_use_tuned_defaults_but_null_keeps_auto(
    tmp_path: Path,
) -> None:
    path = tmp_path / "vision.json"
    path.write_text('{"cameraId": 0}', encoding="utf-8")
    migrated = VisionSettingsStore(path).load()
    assert migrated.white_threshold == 230
    assert migrated.pip_threshold == 235
    assert migrated.automatic_threshold == 0.85
    assert migrated.confirmation_threshold == 0.60
    assert migrated.stationary_frames == 10
    assert migrated.consecutive_value_frames == 3
    assert migrated.min_pip_size_consistency == 0.50
    assert migrated.max_single_pip_center_offset == 0.22
    assert migrated.max_pip_layout_error == 0.15

    path.write_text(
        '{"whiteThreshold": null, "pipThreshold": null}',
        encoding="utf-8",
    )
    automatic = VisionSettingsStore(path).load()
    assert automatic.white_threshold is None
    assert automatic.pip_threshold is None


@pytest.mark.parametrize(
    "settings",
    [
        VisionSettings(automatic_threshold=0.61, confirmation_threshold=0.60),
        VisionSettings(stationary_frames=1),
    ],
)
def test_valid_recognition_settings_are_accepted(settings: VisionSettings) -> None:
    assert settings.recognition_config().stationary_frames >= 1


@pytest.mark.parametrize(
    "payload",
    [
        '{"automaticThreshold": 0.59, "confirmationThreshold": 0.60}',
        '{"stationaryFrames": 0}',
        '{"consecutiveValueFrames": 0}',
        '{"minPipSizeConsistency": 1.1}',
        '{"maxSinglePipCenterOffset": -0.1}',
        '{"maxPipLayoutError": 1.5}',
    ],
)
def test_invalid_recognition_settings_are_rejected(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "vision.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="値が不正"):
        VisionSettingsStore(path).load()
