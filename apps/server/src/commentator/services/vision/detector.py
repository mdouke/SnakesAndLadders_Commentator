"""OpenCV based detector for white dice with black pips on a black background."""

from __future__ import annotations

import itertools
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
    single_pip_center_offset: float = 0.0
    pip_merge_suspected: bool = False
    pip_recounted: bool = False
    pip_layout_error: float = 0.0


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
    single_pip_center_offset: float
    pip_merge_suspected: bool = False
    pip_recounted: bool = False
    pip_layout_error: float = 0.0


@dataclass(frozen=True)
class _PipAnalysis:
    centers: tuple[tuple[float, float], ...]
    mask: np.ndarray
    margin: int
    threshold_value: float
    threshold_is_automatic: bool
    contours: tuple[PipContourDebug, ...]
    pip_size_consistency: float
    single_pip_center_offset: float
    merged_pip_blob: bool = False
    pip_layout_error: float = 0.0


# When a fixed pip threshold leaves a merged blob inside the face, retry the
# count with these lower thresholds. A high threshold keeps washed-out pips
# visible but also darkens the gap between closely spaced pips, so two or three
# neighbouring pips on a tilted 6 can fuse into one blob that is then dropped.
PIP_RECOUNT_THRESHOLD_STEPS = (10, 15, 20)
# Single pips measure about 0.016-0.035 of the inner face in recordings; two
# fused pips measure about 0.05-0.06.
FUSED_PIP_MIN_AREA_RATIO = 0.04

# Canonical pip layouts in pip-spacing units. A 6 has two orientations (columns
# or rows) and 2/3 have two diagonals; 4 and 5 look the same after a 90° turn.
_PIP_LAYOUTS: dict[int, tuple[np.ndarray, ...]] = {
    4: (np.array([(-1, -1), (1, -1), (-1, 1), (1, 1)], dtype=float),),
    5: (np.array([(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)], dtype=float),),
    6: (
        np.array([(-1, -1), (-1, 0), (-1, 1), (1, -1), (1, 0), (1, 1)], dtype=float),
        np.array([(-1, -1), (0, -1), (1, -1), (-1, 1), (0, 1), (1, 1)], dtype=float),
    ),
}
_PIP_PERMUTATIONS = {
    count: np.array(list(itertools.permutations(range(count))))
    for count in _PIP_LAYOUTS
}


def pip_layout_error(centers: tuple[tuple[float, float], ...]) -> float:
    """Return how far 4-6 pips are from the canonical layout.

    Each axis gets its own offset and positive scale: an oblique die is warped
    from a box that also holds part of a side face, so the top face is shifted
    and foreshortened along one axis. The result is the RMS residual divided by
    the smaller fitted pip spacing, minimised over pip assignments. Pips taken
    from a side face, or a missing pip on a washed-out face, do not fit any
    layout and give a large error. 1-3 pips are not checked (always 0.0).
    """
    count = len(centers)
    if count not in _PIP_LAYOUTS:
        return 0.0
    points = np.asarray(centers, dtype=float)
    best = float("inf")
    for layout in _PIP_LAYOUTS[count]:
        assigned = layout[_PIP_PERMUTATIONS[count]]  # (permutations, pips, 2)
        squared = np.zeros(len(assigned))
        scales = np.zeros((len(assigned), 2))
        for axis in range(2):
            template = assigned[:, :, axis]
            observed = points[None, :, axis]
            template_centered = template - template.mean(axis=1, keepdims=True)
            observed_centered = observed - observed.mean()
            scale = (template_centered * observed_centered).sum(axis=1) / (
                (template_centered**2).sum(axis=1)
            )
            residual = observed_centered - scale[:, None] * template_centered
            squared += (residual**2).sum(axis=1)
            scales[:, axis] = scale
        spacing = scales.min(axis=1)
        valid = spacing > 1e-6
        if valid.any():
            error = np.sqrt(squared[valid] / count) / spacing[valid]
            best = min(best, float(error.min()))
    return best


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
            merge_suspected = analysis.merged_pip_blob
            recounted = False
            if merge_suspected and self.pip_threshold is not None:
                for step in PIP_RECOUNT_THRESHOLD_STEPS:
                    retry = self._analyze_pips(
                        warped,
                        collect_debug=self.debug_pips,
                        threshold=self.pip_threshold - step,
                    )
                    if not retry.merged_pip_blob and 1 <= len(retry.centers) <= 6:
                        analysis = retry
                        recounted = True
                        break
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
                    single_pip_center_offset=analysis.single_pip_center_offset,
                    pip_merge_suspected=merge_suspected,
                    pip_recounted=recounted,
                    pip_layout_error=analysis.pip_layout_error,
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
                    single_pip_center_offset=analysis.single_pip_center_offset,
                    pip_merge_suspected=merge_suspected,
                    pip_recounted=recounted,
                    pip_layout_error=analysis.pip_layout_error,
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
        self,
        warped: np.ndarray,
        *,
        collect_debug: bool,
        threshold: int | None = None,
    ) -> _PipAnalysis:
        size = warped.shape[0]
        # The perspective warp already maps the die outline to the image border.
        # Keep only a small guard band: a 12% crop clipped legitimate corner pips
        # on steeply tilted dice and made three-pip faces look like two-pip faces.
        margin = int(size * 0.07)
        inner = warped[margin : size - margin, margin : size - margin]
        inner = cv2.GaussianBlur(inner, (5, 5), 0)
        fixed_threshold = threshold if threshold is not None else self.pip_threshold
        if fixed_threshold is None:
            threshold_value, dark = cv2.threshold(
                inner, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
            )
        else:
            threshold_value, dark = cv2.threshold(
                inner, fixed_threshold, 255, cv2.THRESH_BINARY_INV
            )
        dark = cv2.morphologyEx(
            dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        )
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        inner_area = float(inner.shape[0] * inner.shape[1])
        inner_height, inner_width = inner.shape[:2]
        merged_pip_blob = False
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
            # A rejected blob of at least two pips' area that does not touch
            # the face border is fused pips, not a side face or the outline
            # bleeding in. A fused pair is often rejected for circularity
            # (a peanut shape) rather than size, so judge by area, not reason.
            if (
                reason is not None
                and reason != "area_small"
                and area_ratio >= FUSED_PIP_MIN_AREA_RATIO
                and x > 1 and y > 1
                and x + w < inner_width - 2 and y + h < inner_height - 2
            ):
                merged_pip_blob = True
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
        single_pip_center_offset = (
            float(np.hypot(pips[0][0] - size / 2, pips[0][1] - size / 2) / size)
            if len(pips) == 1 else 0.0
        )
        return _PipAnalysis(
            centers=tuple(pips),
            mask=dark,
            margin=margin,
            threshold_value=float(threshold_value),
            threshold_is_automatic=fixed_threshold is None,
            contours=tuple(contour_debug),
            pip_size_consistency=float(pip_size_consistency),
            single_pip_center_offset=single_pip_center_offset,
            merged_pip_blob=merged_pip_blob,
            pip_layout_error=pip_layout_error(tuple(pips)),
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
