from __future__ import annotations

import unittest

from commentator.domain.board import Board, Jump, JumpType
from commentator.presets import load_board_preset


class BoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.board = load_board_preset()

    def test_standard_board_has_supplied_snakes_and_ladders(self) -> None:
        snakes = {
            jump.start: jump.end for jump in self.board.jumps if jump.type is JumpType.SNAKE
        }
        ladders = {
            jump.start: jump.end for jump in self.board.jumps if jump.type is JumpType.LADDER
        }
        self.assertEqual(
            snakes,
            {98: 78, 95: 75, 93: 73, 87: 24, 64: 60, 62: 19, 56: 53, 49: 11, 48: 26, 16: 6},
        )
        self.assertEqual(
            ladders,
            {1: 38, 4: 14, 9: 31, 21: 42, 28: 84, 36: 44, 51: 67, 71: 91, 80: 100},
        )

    def test_exact_goal_and_overshoot(self) -> None:
        exact = self.board.move(97, 3)
        overshoot = self.board.move(97, 4)
        self.assertEqual(exact.final_position, 100)
        self.assertFalse(exact.overshoot)
        self.assertEqual(overshoot.final_position, 97)
        self.assertEqual(overshoot.normal_position, 97)
        self.assertTrue(overshoot.overshoot)

    def test_snake_and_ladder_are_applied_once(self) -> None:
        ladder = self.board.move(0, 1)
        snake = self.board.move(92, 1)
        self.assertEqual((ladder.normal_position, ladder.final_position), (1, 38))
        self.assertEqual((snake.normal_position, snake.final_position), (93, 73))

    def test_ladder_can_reach_goal(self) -> None:
        result = self.board.move(76, 4)
        self.assertEqual(result.normal_position, 80)
        self.assertEqual(result.final_position, 100)
        assert result.jump is not None
        self.assertEqual(result.jump.type, JumpType.LADDER)

    def test_rejects_chained_jumps(self) -> None:
        with self.assertRaisesRegex(ValueError, "別の開始マス"):
            Board(
                id="invalid",
                name="invalid",
                goal=100,
                jumps=(
                    Jump(JumpType.LADDER, 2, 10),
                    Jump(JumpType.LADDER, 10, 20),
                ),
            )

    def test_minimum_rolls_is_zero_at_goal_and_positive_elsewhere(self) -> None:
        self.assertEqual(self.board.minimum_rolls_from(100), 0)
        self.assertGreater(self.board.minimum_rolls_from(0), 0)
        self.assertEqual(self.board.minimum_rolls_from(99), 1)


if __name__ == "__main__":
    unittest.main()

