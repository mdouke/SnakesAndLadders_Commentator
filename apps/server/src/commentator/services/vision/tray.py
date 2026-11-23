"""Find the dark dice tray inside the search area and follow it when it moves.

The tray is the largest dark region in the search area (the ROI rectangle). Its
convex outline becomes a polygon; everything outside the polygon is blacked out
before recognition and before an image is sent to the vision model, so the
table, shadows and other objects around the tray are never seen.

The outline is updated only while no throw is in progress and the image is
still, and a new outline is adopted only after it was found in the same place
several times. A hand resting on the tray or a passing shadow therefore does
not move the outline, but a tray that was pushed aside is followed.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .motion import Frame, Roi

MIN_AREA_RATIO = 0.15  # of the search area
STABLE_DETECTIONS = 3
# Outlines found in a row agree when they overlap this much. Detection of a
# still tray jitters by under a pixel (overlap 0.997 or more on the real camera).
SAME_SHAPE_IOU = 0.97
# A new outline is adopted when it overlaps the current one less than this.
# 0.92 ignored shifts up to ~20 px (6% of the tray), enough to mask out a die
# lying near the rim (2026-11-23); 0.98 follows a shift of about 5 px.
MOVED_IOU = 0.98
_IOU_SIZE = 480


@dataclass(frozen=True, slots=True)
class TrayShape:
    """Polygon in normalized full-frame coordinates (0..1)."""

    points: tuple[tuple[float, float], ...]
    area_ratio: float  # of the search area

    def pixels(self, width: int, height: int) -> np.ndarray:
        return np.array(
            [[round(x * width), round(y * height)] for x, y in self.points], dtype=np.int32
        )

    def bounds(self, width: int, height: int) -> Roi:
        points = self.pixels(width, height)
        left = int(np.clip(points[:, 0].min(), 0, width - 1))
        top = int(np.clip(points[:, 1].min(), 0, height - 1))
        right = int(np.clip(points[:, 0].max() + 1, left + 1, width))
        bottom = int(np.clip(points[:, 1].max() + 1, top + 1, height))
        return Roi(x=left, y=top, width=right - left, height=bottom - top)


def detect_tray(search: Frame, region: Roi, frame_size: tuple[int, int]) -> TrayShape | None:
    """Find the tray in ``search`` (the frame cropped to ``region``)."""
    gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY) if search.ndim == 3 else search
    height, width = gray.shape[:2]
    if height < 20 or width < 20:
        return None
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    # Two passes: Otsu separates dark from light, then the threshold is moved
    # toward the tray so that grey shadows next to it count as "outside".
    otsu, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = blurred[blurred <= otsu]
    light = blurred[blurred > otsu]
    if dark.size == 0 or light.size == 0:
        return None
    tray_level = float(np.median(dark))
    table_level = float(np.median(light))
    if table_level - tray_level < 30:
        return None  # not enough contrast to tell the tray from the table
    threshold = tray_level + 0.4 * (table_level - tray_level)
    mask = np.asarray((blurred <= threshold) * 255, dtype=np.uint8)
    size = min(height, width)
    # Opening cuts thin dark things touching the tray (stand legs, cables);
    # closing fills the die and its pips.
    open_size = max(5, round(size * 0.08)) | 1
    close_size = max(5, round(size * 0.08)) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_size, open_size)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    area_ratio = cv2.contourArea(largest) / float(height * width)
    if area_ratio < MIN_AREA_RATIO:
        return None
    hull = cv2.convexHull(largest)
    polygon = cv2.approxPolyDP(hull, 0.01 * cv2.arcLength(hull, True), True).reshape(-1, 2)
    if len(polygon) < 3:
        return None
    frame_width, frame_height = frame_size
    points = tuple(
        (
            round((region.x + float(x)) / frame_width, 4),
            round((region.y + float(y)) / frame_height, 4),
        )
        for x, y in polygon
    )
    return TrayShape(points=points, area_ratio=round(area_ratio, 3))


def shape_iou(first: TrayShape, second: TrayShape) -> float:
    """Overlap of two outlines, rasterized at low resolution."""
    canvas = []
    for shape in (first, second):
        mask = np.zeros((_IOU_SIZE, _IOU_SIZE), dtype=np.uint8)
        cv2.fillPoly(mask, [shape.pixels(_IOU_SIZE, _IOU_SIZE)], 1)
        canvas.append(mask.astype(bool))
    union = np.logical_or(*canvas).sum()
    return float(np.logical_and(*canvas).sum() / union) if union else 0.0


class TrayTracker:
    """Keeps the adopted outline and the recent candidates."""

    def __init__(self) -> None:
        self.shape: TrayShape | None = None
        self.updated_at: float | None = None
        self.last_attempt_found: bool | None = None
        self._candidates: list[TrayShape] = []

    def reset(self) -> None:
        self.shape = None
        self.updated_at = None
        self.last_attempt_found = None
        self._candidates.clear()

    def observe(
        self, search: Frame, region: Roi, frame_size: tuple[int, int], *, now: float
    ) -> bool:
        """Look for the tray once; returns True when the adopted outline changed."""
        found = detect_tray(search, region, frame_size)
        self.last_attempt_found = found is not None
        if found is None:
            self._candidates.clear()
            return False
        if self._candidates and shape_iou(self._candidates[-1], found) < SAME_SHAPE_IOU:
            self._candidates.clear()
        self._candidates.append(found)
        if len(self._candidates) < STABLE_DETECTIONS:
            return False
        self._candidates = self._candidates[-STABLE_DETECTIONS:]
        if self.shape is not None and shape_iou(self.shape, found) >= MOVED_IOU:
            return False  # the tray has not moved
        self.shape = found
        self.updated_at = now
        return True
