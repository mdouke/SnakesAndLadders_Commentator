"""Camera boundary with an OpenCV implementation suitable for dependency injection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

import cv2

from .motion import Frame


class CameraConnectionError(RuntimeError):
    """Raised when a camera cannot be opened."""


class CameraDisconnectedError(RuntimeError):
    """Raised when an opened camera no longer returns frames."""


class Capture(Protocol):
    def isOpened(self) -> bool: ...
    def read(self) -> tuple[bool, Frame | None]: ...
    def release(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CameraDevice:
    id: int
    label: str


class CameraStream(Protocol):
    @property
    def device(self) -> CameraDevice: ...
    def read(self) -> Frame: ...
    def close(self) -> None: ...


class OpenCVCameraStream:
    def __init__(self, device: CameraDevice, capture: Capture) -> None:
        self._device = device
        self._capture = capture
        self._closed = False

    @property
    def device(self) -> CameraDevice:
        return self._device

    def read(self) -> Frame:
        if self._closed or not self._capture.isOpened():
            raise CameraDisconnectedError("カメラが切断されています")
        success, frame = self._capture.read()
        if not success or frame is None or frame.size == 0:
            raise CameraDisconnectedError("カメラからフレームを取得できません")
        return frame

    def close(self) -> None:
        if not self._closed:
            self._capture.release()
            self._closed = True

    def __enter__(self) -> OpenCVCameraStream:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class OpenCVCameraProvider:
    """Enumerate and open cameras; hardware access occurs only when methods are called."""

    def __init__(
        self,
        capture_factory: Callable[[int], Capture] | None = None,
        *,
        enumeration_limit: int = 8,
    ) -> None:
        if enumeration_limit <= 0:
            raise ValueError("カメラ列挙数は正の整数にしてください")
        self._capture_factory: Callable[[int], Capture] = (
            capture_factory
            or cast(Callable[[int], Capture], cv2.VideoCapture)
        )
        self._enumeration_limit = enumeration_limit

    def enumerate(self) -> list[CameraDevice]:
        devices: list[CameraDevice] = []
        for device_id in range(self._enumeration_limit):
            capture = self._capture_factory(device_id)
            try:
                if capture.isOpened():
                    devices.append(CameraDevice(device_id, f"Camera {device_id}"))
            finally:
                capture.release()
        return devices

    def open(self, device_id: int) -> OpenCVCameraStream:
        if device_id < 0:
            raise ValueError("カメラIDは0以上にしてください")
        capture = self._capture_factory(device_id)
        if not capture.isOpened():
            capture.release()
            raise CameraConnectionError(f"カメラ {device_id} に接続できません")
        return OpenCVCameraStream(
            CameraDevice(device_id, f"Camera {device_id}"), capture
        )
