"""OpenCV based detector for white dice with black pips on a black background."""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np


@dataclass(frozen=True)
class DieDetection:
    value: int
    box: np.ndarray
    center: tuple[int, int]
    confidence: float
    pip_centers: tuple[tuple[int, int], ...]
    candidate_index: int = 0
    pip_size_consistency: float = 1.0


@dataclass(frozen=True)
class RejectedDieDetection:
    box: np.ndarray
    center: tuple[int, int]
    pip_centers: tuple[tuple[int, int], ...]
    reason: str
    candidate_index: int = 0


@dataclass(frozen=True)
class PipContourDebug:
    contour: np.ndarray
    accepted: bool
    rejection_reason: str | None
    area_ratio: float
    circularity: float
    solidity: float
    aspect_ratio: float


@dataclass(frozen=True)
class PipDetectionDebug:
    candidate_index: int
    box: np.ndarray
    center: tuple[int, int]
    warped_gray: np.ndarray
    pip_mask: np.ndarray
    margin: int
    threshold_value: float
    threshold_is_automatic: bool
    contours: tuple[PipContourDebug, ...]
    counted_pips: int
    pip_size_consistency: float


@dataclass(frozen=True)
class _PipAnalysis:
    centers: tuple[tuple[float, float], ...]
    mask: np.ndarray
    margin: int
    threshold_value: float
    threshold_is_automatic: bool
    contours: tuple[PipContourDebug, ...]
    pip_size_consistency: float


def _order_points(points: np.ndarray) -> np.ndarray:
    """Return four distinct corners clockwise from a deterministic top-left."""
    selected = points.astype(np.float32).reshape(4, 2)
    center = selected.mean(axis=0)
    # Image y coordinates grow downward, so increasing atan2 angles walk the
    # corners clockwise: top -> right -> bottom -> left.
    angles = np.arctan2(selected[:, 1] - center[1], selected[:, 0] - center[0])
    clockwise = selected[np.argsort(angles)]
    start = min(
        range(4),
        key=lambda index: (
            float(clockwise[index].sum()),
            float(clockwise[index, 1]),
            float(clockwise[index, 0]),
        ),
    )
    return np.roll(clockwise, -start, axis=0).astype(np.float32)


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
        min_pip_circularity: float = 0.75,
        min_pip_solidity: float = 0.70,
        white_threshold: int | None = None,
        pip_threshold: int | None = None,
        debug_pips: bool = False,
    ) -> None:
        self.min_die_area_ratio = min_die_area_ratio
        self.max_die_area_ratio = max_die_area_ratio
        self.min_pip_area_ratio = min_pip_area_ratio
        self.max_pip_area_ratio = max_pip_area_ratio
        self.max_die_aspect = max_die_aspect
        self.min_rectangularity = min_rectangularity
        self.min_solidity = min_solidity
        self.min_pip_circularity = min_pip_circularity
        self.min_pip_solidity = min_pip_solidity
        self.white_threshold = white_threshold
        self.pip_threshold = pip_threshold
        self.debug_pips = debug_pips
        self.last_white_threshold = 0.0
        self.last_rejected_detections: tuple[RejectedDieDetection, ...] = ()
        self.last_pip_debug: tuple[PipDetectionDebug, ...] = ()

    def detect(
        self, frame: np.ndarray
    ) -> tuple[list[DieDetection], np.ndarray, int]:
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
        rejected: list[RejectedDieDetection] = []
        pip_debug: list[PipDetectionDebug] = []
        rejected_count = 0

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
            analysis = self._analyze_pips(warped, collect_debug=self.debug_pips)
            pip_local = list(analysis.centers)
            value = len(pip_local)
            box_int = np.rint(box).astype(np.int32)
            center = tuple(np.mean(box, axis=0).round().astype(int))
            if self.debug_pips:
                pip_debug.append(PipDetectionDebug(
                    candidate_index=0,
                    box=box_int.copy(),
                    center=center,
                    warped_gray=warped.copy(),
                    pip_mask=analysis.mask.copy(),
                    margin=analysis.margin,
                    threshold_value=analysis.threshold_value,
                    threshold_is_automatic=analysis.threshold_is_automatic,
                    contours=analysis.contours,
                    counted_pips=value,
                    pip_size_consistency=analysis.pip_size_consistency,
                ))
            if not 1 <= value <= 6:
                rejected_count += 1
                inverse = np.linalg.inv(matrix)
                if pip_local:
                    pip_array = np.array(pip_local, dtype=np.float32).reshape(-1, 1, 2)
                    pip_global = cv2.perspectiveTransform(pip_array, inverse).reshape(-1, 2)
                    pip_centers = tuple(
                        (int(round(x)), int(round(y))) for x, y in pip_global
                    )
                else:
                    pip_centers = ()
                rejected.append(RejectedDieDetection(
                    box=box_int,
                    center=center,
                    pip_centers=pip_centers,
                    reason=f"pip_count_{value}",
                ))
                continue

            inverse = np.linalg.inv(matrix)
            pip_array = np.array(pip_local, dtype=np.float32).reshape(-1, 1, 2)
            pip_global = cv2.perspectiveTransform(pip_array, inverse).reshape(-1, 2)
            pip_centers = tuple((int(round(x)), int(round(y))) for x, y in pip_global)
            shape_score = max(0.0, 1.0 - abs(1.0 - aspect) / max(self.max_die_aspect - 1.0, 0.01))
            confidence = float(
                np.clip(0.45 * rectangularity + 0.25 * solidity + 0.30 * shape_score, 0, 1)
            )
            detections.append(
                DieDetection(
                    value,
                    box_int,
                    center,
                    confidence,
                    pip_centers,
                    pip_size_consistency=analysis.pip_size_consistency,
                )
            )

        ordered_centers = sorted(
            {item.center for item in detections}
            | {item.center for item in rejected},
            key=lambda point: (point[1], point[0]),
        )
        index_by_center = {
            center: index for index, center in enumerate(ordered_centers, 1)
        }
        detections = [
            replace(item, candidate_index=index_by_center[item.center])
            for item in detections
        ]
        detections.sort(key=lambda item: item.candidate_index)
        rejected = [
            replace(item, candidate_index=index_by_center[item.center])
            for item in rejected
        ]
        rejected.sort(key=lambda item: item.candidate_index)
        pip_debug = [
            replace(item, candidate_index=index_by_center[item.center])
            for item in pip_debug
        ]
        pip_debug.sort(key=lambda item: item.candidate_index)
        self.last_rejected_detections = tuple(rejected)
        self.last_pip_debug = tuple(pip_debug)
        return detections, white, rejected_count

    @staticmethod
    def _warp_die(gray: np.ndarray, box: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        size = 180
        destination = np.array(
            [[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], dtype=np.float32
        )
        matrix = cv2.getPerspectiveTransform(box, destination)
        return cv2.warpPerspective(gray, matrix, (size, size)), matrix

    def _find_pips(self, warped: np.ndarray) -> list[tuple[float, float]]:
        return list(self._analyze_pips(warped, collect_debug=False).centers)

    def _analyze_pips(
        self, warped: np.ndarray, *, collect_debug: bool
    ) -> _PipAnalysis:
        size = warped.shape[0]
        # The perspective warp already maps the die outline to the image border.
        # Keep only a small guard band: a 12% crop clipped legitimate corner pips
        # on steeply tilted dice and made three-pip faces look like two-pip faces.
        margin = int(size * 0.07)
        inner = warped[margin : size - margin, margin : size - margin]
        inner = cv2.GaussianBlur(inner, (5, 5), 0)
        if self.pip_threshold is None:
            threshold_value, dark = cv2.threshold(
                inner, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
            )
        else:
            threshold_value, dark = cv2.threshold(
                inner, self.pip_threshold, 255, cv2.THRESH_BINARY_INV
            )
        dark = cv2.morphologyEx(
            dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        )
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        inner_area = float(inner.shape[0] * inner.shape[1])
        pips: list[tuple[float, float]] = []
        pip_area_ratios: list[float] = []
        contour_debug: list[PipContourDebug] = []

        for contour in contours:
            # Recessed pips often catch a specular highlight that cuts a notch
            # into the dark blob, turning it into a crescent whose raw contour
            # fails the circularity test even though the mask clearly shows a
            # pip. Judge shape on the convex hull instead, and use solidity
            # (contour area / hull area) to keep rejecting thin arcs and edges.
            hull = cv2.convexHull(contour)
            area = cv2.contourArea(hull)
            area_ratio = area / inner_area
            perimeter = cv2.arcLength(hull, True)
            solidity = cv2.contourArea(contour) / max(area, 1.0)
            circularity = (
                4.0 * np.pi * area / (perimeter * perimeter)
                if perimeter > 0 else 0.0
            )
            x, y, w, h = cv2.boundingRect(hull)
            aspect = max(w, h) / max(min(w, h), 1)
            reason: str | None = None
            if perimeter <= 0 or area_ratio < self.min_pip_area_ratio:
                reason = "area_small"
            elif area_ratio > self.max_pip_area_ratio:
                reason = "area_large"
            elif circularity < self.min_pip_circularity:
                reason = "circularity"
            elif solidity < self.min_pip_solidity:
                reason = "solidity"
            elif aspect > 1.55:
                reason = "aspect"
            accepted = reason is None
            if collect_debug:
                debug_contour = contour.copy()
                debug_contour[:, 0, 0] += margin
                debug_contour[:, 0, 1] += margin
                contour_debug.append(PipContourDebug(
                    contour=debug_contour,
                    accepted=accepted,
                    rejection_reason=reason,
                    area_ratio=float(area_ratio),
                    circularity=float(circularity),
                    solidity=float(solidity),
                    aspect_ratio=float(aspect),
                ))
            moments = cv2.moments(hull)
            if accepted and moments["m00"]:
                cx = moments["m10"] / moments["m00"] + margin
                cy = moments["m01"] / moments["m00"] + margin
                pips.append((cx, cy))
                pip_area_ratios.append(float(area_ratio))
        pip_size_consistency = (
            min(pip_area_ratios) / max(pip_area_ratios)
            if len(pip_area_ratios) >= 2 else 1.0
        )
        return _PipAnalysis(
            centers=tuple(pips),
            mask=dark,
            margin=margin,
            threshold_value=float(threshold_value),
            threshold_is_automatic=self.pip_threshold is None,
            contours=tuple(contour_debug),
            pip_size_consistency=float(pip_size_consistency),
        )

    @staticmethod
    def annotate(frame: np.ndarray, detections: list[DieDetection]) -> np.ndarray:
        output = frame.copy()
        for index, die in enumerate(detections, 1):
            cv2.polylines(output, [die.box], True, (0, 220, 0), 3, cv2.LINE_AA)
            for point in die.pip_centers:
                cv2.circle(output, point, 7, (0, 180, 255), 2, cv2.LINE_AA)
                cv2.circle(output, point, 2, (0, 180, 255), -1, cv2.LINE_AA)
            display_index = die.candidate_index or index
            label = f"#{display_index}  {die.value}  ({die.confidence:.0%})"
            x = int(die.box[:, 0].min())
            y = max(28, int(die.box[:, 1].min()) - 10)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.68, 2)
            cv2.rectangle(output, (x - 3, y - th - 7), (x + tw + 5, y + 5), (0, 100, 0), -1)
            cv2.putText(
                output,
                label,
                (x, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.68,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
        return output
