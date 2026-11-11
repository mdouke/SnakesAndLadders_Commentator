"""OpenCV based detector for white dice with black pips on a black background."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class DieDetection:
    value: int
    box: np.ndarray
    center: tuple[int, int]
    confidence: float
    pip_centers: tuple[tuple[int, int], ...]


def _order_points(points: np.ndarray) -> np.ndarray:
    points = points.astype(np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(sums)]      # top-left
    ordered[2] = points[np.argmax(sums)]      # bottom-right
    ordered[1] = points[np.argmin(diffs)]     # top-right
    ordered[3] = points[np.argmax(diffs)]     # bottom-left
    return ordered


class DiceDetector:
    """Detect dice and count their pips.

    The detector intentionally assumes high contrast: a black background, white
    dice, and black pips. Rotation is supported through perspective warping.
    """

    def __init__(
        self,
        min_die_area_ratio: float = 0.003,
        max_die_area_ratio: float = 0.45,
        min_pip_area_ratio: float = 0.005,
        max_pip_area_ratio: float = 0.075,
        max_die_aspect: float = 1.45,
        min_rectangularity: float = 0.68,
        min_solidity: float = 0.88,
        min_pip_circularity: float = 0.50,
        white_threshold: int | None = None,
    ) -> None:
        self.min_die_area_ratio = min_die_area_ratio
        self.max_die_area_ratio = max_die_area_ratio
        self.min_pip_area_ratio = min_pip_area_ratio
        self.max_pip_area_ratio = max_pip_area_ratio
        self.max_die_aspect = max_die_aspect
        self.min_rectangularity = min_rectangularity
        self.min_solidity = min_solidity
        self.min_pip_circularity = min_pip_circularity
        self.white_threshold = white_threshold
        self.last_white_threshold = 0.0

    def detect(self, frame: np.ndarray) -> tuple[list[DieDetection], np.ndarray]:
        if frame is None or frame.size == 0:
            raise ValueError("入力画像が空です")

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame.copy()
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        if self.white_threshold is None:
            threshold_value, white = cv2.threshold(
                blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
        else:
            threshold_value, white = cv2.threshold(
                blurred, int(self.white_threshold), 255, cv2.THRESH_BINARY
            )
        self.last_white_threshold = float(threshold_value)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, kernel, iterations=2)
        white = cv2.morphologyEx(white, cv2.MORPH_OPEN, kernel, iterations=1)

        contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        image_area = float(gray.shape[0] * gray.shape[1])
        detections: list[DieDetection] = []

        for contour in contours:
            area = cv2.contourArea(contour)
            ratio = area / image_area
            if not self.min_die_area_ratio <= ratio <= self.max_die_area_ratio:
                continue

            rect = cv2.minAreaRect(contour)
            rw, rh = rect[1]
            if min(rw, rh) < 20:
                continue
            aspect = max(rw, rh) / min(rw, rh)
            rectangularity = area / max(rw * rh, 1.0)
            hull_area = cv2.contourArea(cv2.convexHull(contour))
            solidity = area / max(hull_area, 1.0)
            if (
                aspect > self.max_die_aspect
                or rectangularity < self.min_rectangularity
                or solidity < self.min_solidity
            ):
                continue

            box = _order_points(cv2.boxPoints(rect))
            warped, matrix = self._warp_die(gray, box)
            pip_local = self._find_pips(warped)
            value = len(pip_local)
            if not 1 <= value <= 6:
                continue

            inverse = np.linalg.inv(matrix)
            pip_array = np.array(pip_local, dtype=np.float32).reshape(-1, 1, 2)
            pip_global = cv2.perspectiveTransform(pip_array, inverse).reshape(-1, 2)
            pip_centers = tuple((int(round(x)), int(round(y))) for x, y in pip_global)
            center = tuple(np.mean(box, axis=0).round().astype(int))
            shape_score = max(0.0, 1.0 - abs(1.0 - aspect) / max(self.max_die_aspect - 1.0, 0.01))
            confidence = float(
                np.clip(0.45 * rectangularity + 0.25 * solidity + 0.30 * shape_score, 0, 1)
            )
            detections.append(
                DieDetection(value, np.rint(box).astype(np.int32), center, confidence, pip_centers)
            )

        detections.sort(key=lambda item: (item.center[1], item.center[0]))
        return detections, white

    @staticmethod
    def _warp_die(gray: np.ndarray, box: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        size = 180
        destination = np.array(
            [[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], dtype=np.float32
        )
        matrix = cv2.getPerspectiveTransform(box, destination)
        return cv2.warpPerspective(gray, matrix, (size, size)), matrix

    def _find_pips(self, warped: np.ndarray) -> list[tuple[float, float]]:
        size = warped.shape[0]
        margin = int(size * 0.12)
        inner = warped[margin : size - margin, margin : size - margin]
        inner = cv2.GaussianBlur(inner, (5, 5), 0)
        _, dark = cv2.threshold(inner, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        dark = cv2.morphologyEx(
            dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        )
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        inner_area = float(inner.shape[0] * inner.shape[1])
        pips: list[tuple[float, float]] = []

        for contour in contours:
            area = cv2.contourArea(contour)
            area_ratio = area / inner_area
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0 or not self.min_pip_area_ratio <= area_ratio <= self.max_pip_area_ratio:
                continue
            circularity = 4.0 * np.pi * area / (perimeter * perimeter)
            x, y, w, h = cv2.boundingRect(contour)
            aspect = max(w, h) / max(min(w, h), 1)
            if circularity < self.min_pip_circularity or aspect > 1.55:
                continue
            moments = cv2.moments(contour)
            if moments["m00"]:
                cx = moments["m10"] / moments["m00"] + margin
                cy = moments["m01"] / moments["m00"] + margin
                pips.append((cx, cy))
        return pips

    @staticmethod
    def annotate(frame: np.ndarray, detections: list[DieDetection]) -> np.ndarray:
        output = frame.copy()
        for index, die in enumerate(detections, 1):
            cv2.polylines(output, [die.box], True, (0, 220, 0), 3, cv2.LINE_AA)
            for point in die.pip_centers:
                cv2.circle(output, point, 7, (0, 180, 255), 2, cv2.LINE_AA)
                cv2.circle(output, point, 2, (0, 180, 255), -1, cv2.LINE_AA)
            label = f"#{index}  {die.value}  ({die.confidence:.0%})"
            x = int(die.box[:, 0].min())
            y = max(28, int(die.box[:, 1].min()) - 10)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.68, 2)
            cv2.rectangle(output, (x - 3, y - th - 7), (x + tw + 5, y + 5), (0, 100, 0), -1)
            cv2.putText(output, label, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 255, 255), 2, cv2.LINE_AA)
        return output
