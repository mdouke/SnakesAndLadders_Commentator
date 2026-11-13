"""Frame and ROI utilities used by dice recognition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class Roi:
    x: int
    y: int
    width: int
    height: int

    def crop(self, frame: Frame) -> Frame:
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("ROIの座標とサイズが不正です")
        frame_height, frame_width = frame.shape[:2]
        if self.x + self.width > frame_width or self.y + self.height > frame_height:
            raise ValueError("ROIがフレームの範囲外です")
        return frame[self.y : self.y + self.height, self.x : self.x + self.width]


def frame_motion_score(previous: Frame, current: Frame) -> float:
    """Return normalized mean absolute difference in the range 0..1."""
    if previous.size == 0 or current.size == 0:
        raise ValueError("動き判定の入力画像が空です")
    if previous.shape != current.shape:
        raise ValueError("動き判定の画像サイズが一致しません")
    previous_gray = _grayscale(previous)
    current_gray = _grayscale(current)
    difference = np.abs(previous_gray.astype(np.float32) - current_gray.astype(np.float32))
    return float(np.clip(np.mean(difference) / 255.0, 0.0, 1.0))


def _grayscale(frame: Frame) -> NDArray[np.float32]:
    if frame.ndim == 2:
        return frame.astype(np.float32)
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError("画像はグレースケールまたは3チャンネル以上で指定してください")
    blue = frame[..., 0].astype(np.float32)
    green = frame[..., 1].astype(np.float32)
    red = frame[..., 2].astype(np.float32)
    return cast(
        NDArray[np.float32],
        0.114 * blue + 0.587 * green + 0.299 * red,
    )
