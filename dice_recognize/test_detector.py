import unittest

import cv2
import numpy as np

from dice_detector import DiceDetector
from generate_demo import draw_die


class DiceDetectorTest(unittest.TestCase):
    def test_all_values_and_rotations(self):
        canvas = np.zeros((700, 1050, 3), dtype=np.uint8)
        specs = [((150, 150), 120, 1, -8), ((500, 150), 130, 2, 9), ((870, 150), 125, 3, -4),
                 ((150, 500), 130, 4, 7), ((500, 500), 125, 5, -10), ((870, 500), 130, 6, 5)]
        for args in specs:
            draw_die(canvas, *args)
        detected, _ = DiceDetector().detect(canvas)
        self.assertEqual(sorted(d.value for d in detected), [1, 2, 3, 4, 5, 6])

    def test_manual_white_threshold_rejects_dim_reflection(self):
        canvas = np.zeros((300, 500, 3), dtype=np.uint8)
        canvas[30:130, 30:180] = 175  # 背景上の明るい反射
        draw_die(canvas, (360, 160), 120, 4, 0)
        detector = DiceDetector(white_threshold=220)
        detected, mask = detector.detect(canvas)
        self.assertEqual([die.value for die in detected], [4])
        self.assertEqual(int(mask[70, 80]), 0)
        self.assertEqual(int(mask[126, 326]), 0)  # 黒点
        self.assertEqual(int(mask[110, 360]), 255)  # サイコロの白い面

if __name__ == "__main__":
    unittest.main()
