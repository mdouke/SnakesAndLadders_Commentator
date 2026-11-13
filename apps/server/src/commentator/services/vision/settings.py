"""Validated, resolution-independent vision settings persisted as JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .motion import Frame, Roi


@dataclass(frozen=True, slots=True)
class NormalizedRoi:
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if not all(0 <= value <= 1 for value in values):
            raise ValueError("ROIは0〜1の比率で指定してください")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("ROIの幅と高さは0より大きくしてください")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("ROIがフレームの範囲外です")

    def to_pixels(self, frame: Frame) -> Roi:
        frame_height, frame_width = frame.shape[:2]
        x = min(round(self.x * frame_width), frame_width - 1)
        y = min(round(self.y * frame_height), frame_height - 1)
        right = min(round((self.x + self.width) * frame_width), frame_width)
        bottom = min(round((self.y + self.height) * frame_height), frame_height)
        return Roi(x=x, y=y, width=max(1, right - x), height=max(1, bottom - y))

    def crop(self, frame: Frame) -> Frame:
        return self.to_pixels(frame).crop(frame)


@dataclass(frozen=True, slots=True)
class VisionSettings:
    camera_id: int | None = None
    roi: NormalizedRoi = NormalizedRoi()
    white_threshold: int | None = None

    def __post_init__(self) -> None:
        if self.camera_id is not None and self.camera_id < 0:
            raise ValueError("カメラIDは0以上にしてください")
        if self.white_threshold is not None and not 0 <= self.white_threshold <= 255:
            raise ValueError("白領域しきい値は0〜255で指定してください")


class VisionSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> VisionSettings:
        if not self.path.exists():
            return VisionSettings()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("サイコロ認識設定を読み込めません") from error
        if not isinstance(value, dict):
            raise ValueError("サイコロ認識設定の形式が不正です")
        try:
            camera_id = value.get("cameraId")
            roi = value.get("roi", {})
            if not isinstance(roi, dict):
                raise TypeError
            return VisionSettings(
                camera_id=int(camera_id) if camera_id is not None else None,
                roi=NormalizedRoi(
                    x=float(roi.get("x", 0.0)),
                    y=float(roi.get("y", 0.0)),
                    width=float(roi.get("width", 1.0)),
                    height=float(roi.get("height", 1.0)),
                ),
                white_threshold=(
                    int(value["whiteThreshold"])
                    if value.get("whiteThreshold") is not None else None
                ),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("サイコロ認識設定の値が不正です") from error

    def save(self, settings: VisionSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        roi: dict[str, Any] = asdict(settings.roi)
        payload = {
            "cameraId": settings.camera_id,
            "roi": roi,
            "whiteThreshold": settings.white_threshold,
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
