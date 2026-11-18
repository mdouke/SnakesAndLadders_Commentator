"""Render the admin ROI preview image (requires the optional vision extras)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

from .motion import Frame
from .settings import NormalizedRoi

PREVIEW_MAX_WIDTH = 640
JPEG_QUALITY = 80
_ROI_COLOR = (0, 200, 0)
_DIE_COLOR = (0, 160, 255)


def render_preview_jpeg(
    frame: Frame,
    *,
    roi: NormalizedRoi | None,
    detections: Sequence[Any] = (),
) -> bytes:
    """Draw the ROI and detected dice on a copy of the frame and encode it as JPEG.

    Detection boxes and centers are in ROI-cropped coordinates, as produced by
    ``DiceRecognitionPipeline``; they are shifted back onto the full frame here.
    """
    image: Frame = np.asarray(
        cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy(),
        dtype=np.uint8,
    )
    region = (roi or NormalizedRoi()).to_pixels(image)
    cv2.rectangle(
        image,
        (region.x, region.y),
        (region.x + region.width - 1, region.y + region.height - 1),
        _ROI_COLOR,
        2,
    )
    offset = np.array([region.x, region.y], dtype=np.int32)
    for detection in detections:
        box = np.asarray(detection.box, dtype=np.int32).reshape(-1, 2) + offset
        cv2.polylines(image, [box], isClosed=True, color=_DIE_COLOR, thickness=2)
        center_x = int(detection.center[0]) + region.x
        center_y = int(detection.center[1]) + region.y
        cv2.putText(
            image,
            str(detection.value),
            (center_x - 10, center_y - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            _DIE_COLOR,
            2,
            cv2.LINE_AA,
        )
    height, width = image.shape[:2]
    if width > PREVIEW_MAX_WIDTH:
        scale = PREVIEW_MAX_WIDTH / width
        image = np.asarray(
            cv2.resize(
                image, (PREVIEW_MAX_WIDTH, max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            ),
            dtype=np.uint8,
        )
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("プレビュー画像をJPEGに変換できません")
    return bytes(encoded.tobytes())
