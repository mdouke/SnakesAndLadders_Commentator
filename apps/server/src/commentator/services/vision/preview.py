"""Render admin preview images (requires the optional vision extras).

Three views help when recognition fails in a new environment:
- ``overlay``: the full frame with the ROI, accepted dice (orange) and rejected
  die-like outlines (red, with the reason)
- ``mask``: the white mask inside the ROI, i.e. what the outline detector sees
- ``pips``: the warped face and pip mask of every candidate (same as the bench)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

from .detector import PipContourDebug, PipDetectionDebug
from .motion import Frame
from .settings import NormalizedRoi

PREVIEW_MAX_WIDTH = 640
JPEG_QUALITY = 80
_ROI_COLOR = (0, 200, 0)
_DIE_COLOR = (0, 160, 255)
_REJECTED_COLOR = (0, 0, 255)
_TRAY_COLOR = (255, 200, 0)


def _encode(image: Frame, *, max_width: int = PREVIEW_MAX_WIDTH) -> bytes:
    height, width = image.shape[:2]
    if width > max_width:
        scale = max_width / width
        image = np.asarray(
            cv2.resize(
                image, (max_width, max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            ),
            dtype=np.uint8,
        )
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("プレビュー画像をJPEGに変換できません")
    return bytes(encoded.tobytes())


def _bgr(frame: Frame) -> Frame:
    return np.asarray(
        cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy(),
        dtype=np.uint8,
    )


def _draw_dice(
    image: Frame,
    offset: tuple[int, int],
    detections: Sequence[Any],
    rejected: Sequence[Any],
) -> None:
    shift = np.array(offset, dtype=np.int32)
    for item in rejected:
        box = np.asarray(item.box, dtype=np.int32).reshape(-1, 2) + shift
        cv2.polylines(image, [box], isClosed=True, color=_REJECTED_COLOR, thickness=2)
        x = int(box[:, 0].min())
        y = max(16, int(box[:, 1].min()) - 6)
        cv2.putText(image, f"NG {item.reason}", (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, _REJECTED_COLOR, 2, cv2.LINE_AA)
    for detection in detections:
        box = np.asarray(detection.box, dtype=np.int32).reshape(-1, 2) + shift
        cv2.polylines(image, [box], isClosed=True, color=_DIE_COLOR, thickness=2)
        center_x = int(detection.center[0]) + offset[0]
        center_y = int(detection.center[1]) + offset[1]
        cv2.putText(image, str(detection.value), (center_x - 10, center_y - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, _DIE_COLOR, 2, cv2.LINE_AA)


def render_preview_jpeg(
    frame: Frame,
    *,
    roi: NormalizedRoi | None,
    detections: Sequence[Any] = (),
    rejected: Sequence[Any] = (),
    crop_origin: tuple[int, int] | None = None,
    tray: np.ndarray | None = None,
) -> bytes:
    """Draw the ROI, the tray outline and dice on a copy of the full frame.

    Detection boxes and centers are in cropped coordinates, as produced by
    ``DiceRecognitionPipeline``; ``crop_origin`` is where that crop starts (the
    ROI corner, or the tray's bounding box when the tray is detected).
    """
    image = _bgr(frame)
    region = (roi or NormalizedRoi()).to_pixels(image)
    cv2.rectangle(
        image,
        (region.x, region.y),
        (region.x + region.width - 1, region.y + region.height - 1),
        _ROI_COLOR,
        2,
    )
    if tray is not None:
        # Darken everything outside the tray so the operator sees what is used.
        outside = np.full(image.shape[:2], 255, dtype=np.uint8)
        cv2.fillPoly(outside, [tray.astype(np.int32)], 0)
        image[outside > 0] = (image[outside > 0] * 0.45).astype(np.uint8)
        cv2.polylines(image, [tray.astype(np.int32)], True, _TRAY_COLOR, 3, cv2.LINE_AA)
    origin = crop_origin or (region.x, region.y)
    _draw_dice(image, origin, detections, rejected)
    return _encode(image)


def render_mask_jpeg(
    mask: Frame, *, detections: Sequence[Any] = (), rejected: Sequence[Any] = ()
) -> bytes:
    """The ROI white mask: white areas are what the outline detector can use."""
    image = _bgr(mask)
    _draw_dice(image, (0, 0), detections, rejected)
    return _encode(image)


def render_pips_jpeg(items: tuple[PipDetectionDebug, ...]) -> bytes:
    image = render_pip_debug(items)
    if image is None:
        image = np.zeros((120, 360, 3), dtype=np.uint8)
        cv2.putText(image, "no die candidate", (20, 68), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (200, 200, 200), 2, cv2.LINE_AA)
    return _encode(image, max_width=720)


def _pip_rejection_label(contour: PipContourDebug) -> str:
    reason = contour.rejection_reason
    if reason == "area_small":
        return f"area< {contour.area_ratio:.3f}"
    if reason == "area_large":
        return f"area> {contour.area_ratio:.3f}"
    if reason == "circularity":
        return f"circ {contour.circularity:.2f}"
    if reason == "solidity":
        return f"solid {contour.solidity:.2f}"
    if reason == "aspect":
        return f"aspect {contour.aspect_ratio:.2f}"
    return "rejected"


def _merge_label(item: PipDetectionDebug) -> str:
    if item.pip_recounted:
        return "recount"
    return "yes" if item.pip_merge_suspected else "no"


def render_pip_debug(items: tuple[PipDetectionDebug, ...]) -> Frame | None:
    """Render the exact pip-counting inputs and decisions for the live GUI."""
    rows: list[Frame] = []
    for item in items:
        face = cv2.cvtColor(item.warped_gray, cv2.COLOR_GRAY2BGR)
        mask_gray = np.full(item.warped_gray.shape, 192, dtype=np.uint8)
        inner_height, inner_width = item.pip_mask.shape[:2]
        margin = item.margin
        mask_gray[
            margin : margin + inner_height,
            margin : margin + inner_width,
        ] = 255 - item.pip_mask
        mask = cv2.cvtColor(mask_gray, cv2.COLOR_GRAY2BGR)
        for contour in item.contours:
            color = (0, 190, 0) if contour.accepted else (0, 0, 255)
            cv2.polylines(face, [contour.contour], True, color, 2, cv2.LINE_AA)
            cv2.polylines(mask, [contour.contour], True, color, 2, cv2.LINE_AA)
            if not contour.accepted:
                x, y, _, _ = cv2.boundingRect(contour.contour)
                cv2.putText(
                    face,
                    _pip_rejection_label(contour),
                    (max(0, x - 3), max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.34,
                    color,
                    1,
                    cv2.LINE_AA,
                )
        header = np.zeros((28, 360, 3), dtype=np.uint8)
        cv2.putText(
            header,
            (
                f"#{item.candidate_index}  pips={item.counted_pips}  "
                f"PipThr={item.threshold_value:.0f} "
                f"{'auto' if item.threshold_is_automatic else 'fixed'}   "
                f"PipSize={item.pip_size_consistency:.2f}   "
                f"PipCenter={item.single_pip_center_offset:.2f}   "
                f"Merge={_merge_label(item)}   "
                f"Layout={item.pip_layout_error:.2f}   "
                "face / count mask"
            ),
            (5, 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        rows.append(np.vstack((header, np.hstack((face, mask)))))
    return np.vstack(rows) if rows else None
