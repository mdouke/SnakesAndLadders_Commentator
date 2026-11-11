"""Generate a deterministic sample image for trying the GUI."""

from pathlib import Path

import cv2
import numpy as np


PIPS = {
    1: [(0, 0)],
    2: [(-1, -1), (1, 1)],
    3: [(-1, -1), (0, 0), (1, 1)],
    4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
    5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
    6: [(-1, -1), (-1, 0), (-1, 1), (1, -1), (1, 0), (1, 1)],
}


def draw_die(canvas, center, size, value, angle=0):
    die = np.zeros((size + 30, size + 30, 3), dtype=np.uint8)
    cv2.rectangle(die, (15, 15), (15 + size, 15 + size), (255, 255, 255), -1)
    radius = max(7, size // 13)
    spacing = size * 0.28
    cx = cy = 15 + size / 2
    for px, py in PIPS[value]:
        cv2.circle(die, (round(cx + px * spacing), round(cy + py * spacing)), radius, (0, 0, 0), -1)
    matrix = cv2.getRotationMatrix2D((die.shape[1] / 2, die.shape[0] / 2), angle, 1)
    rotated = cv2.warpAffine(die, matrix, (die.shape[1], die.shape[0]))
    x, y = center[0] - rotated.shape[1] // 2, center[1] - rotated.shape[0] // 2
    roi = canvas[y : y + rotated.shape[0], x : x + rotated.shape[1]]
    mask = cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY) > 0
    roi[mask] = rotated[mask]


def main():
    canvas = np.zeros((650, 1000, 3), dtype=np.uint8)
    specs = [((180, 180), 150, 1, -9), ((500, 180), 170, 4, 7), ((810, 190), 145, 6, -4),
             ((310, 475), 160, 3, 12), ((690, 470), 180, 5, -10)]
    for args in specs:
        draw_die(canvas, *args)
    output = Path(__file__).with_name("demo_dice.png")
    cv2.imwrite(str(output), canvas)
    print(output)


if __name__ == "__main__":
    main()
