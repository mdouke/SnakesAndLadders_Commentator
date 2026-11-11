from __future__ import annotations

import unittest
from datetime import UTC, datetime

from commentator.domain.game import GameState, GameStatus, PlayerState
from commentator.presets import load_board_preset


def player(number: int, position: int = 0) -> PlayerState:
    return PlayerState(
        id=f"p{number}",
        name=f"プレイヤー{number}",
        color=("red", "blue", "green")[number - 1],
        position=position,
    )


class GameTest(unittest.TestCase):
    def setUp(self) -> None:
        self.board = load_board_preset()
        self.now = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)

    def test_two_player_game_ends_when_first_player_reaches_goal(self) -> None:
        game = GameState.create(self.board, [player(1, 97), player(2, 42)])
        game.start(now=self.now)
        turn = game.roll(3, now=self.now)

        self.assertEqual(turn.final_position, 100)
        self.assertEqual(game.status, GameStatus.COMPLETED)
        self.assertTrue(game.players[0].reached_goal)
        self.assertEqual(game.players[0].rank, 1)
        self.assertTrue(game.players[0].rank_confirmed)
        self.assertFalse(game.players[1].reached_goal)
        self.assertEqual(game.players[1].position, 42)
        self.assertEqual(game.players[1].rank, 2)
        self.assertTrue(game.players[1].rank_confirmed)

    def test_three_player_game_skips_goal_player_and_ends_after_two_goals(self) -> None:
        first = player(1, 97)
        second = player(2, 20)
        third = player(3, 99)
        game = GameState.create(self.board, [first, second, third])
        game.start(now=self.now)

        game.roll(3, now=self.now)
        self.assertEqual(game.current_player.id, "p2")
        game.roll(1, now=self.now)  # 21 -> 42 via ladder
        self.assertEqual(game.current_player.id, "p3")
        game.roll(1, now=self.now)

        self.assertEqual(game.status, GameStatus.COMPLETED)
        self.assertEqual((first.rank, third.rank, second.rank), (1, 2, 3))
        self.assertTrue(first.reached_goal)
        self.assertTrue(third.reached_goal)
        self.assertFalse(second.reached_goal)
        self.assertTrue(second.rank_confirmed)

    def test_overshoot_is_recorded_without_movement(self) -> None:
        first = player(1, 99)
        game = GameState.create(self.board, [first, player(2)])
        game.start(now=self.now)
        turn = game.roll(2, now=self.now)

        self.assertEqual((turn.before, turn.normal_position, turn.final_position), (99, 99, 99))
        self.assertTrue(turn.overshoot)
        self.assertEqual(first.statistics.rolls, [2])
        self.assertEqual(first.statistics.normal_movement_total, 0)
        self.assertEqual(first.statistics.actual_position_change_total, 0)
        self.assertEqual(first.statistics.overshoot_count, 1)

    def test_statistics_include_absolute_snake_and_ladder_position_changes(self) -> None:
        first = player(1)
        second = player(2, 92)
        game = GameState.create(self.board, [first, second])
        game.start(now=self.now)

        game.roll(1, now=self.now)  # 0 -> 1 -> 38
        game.roll(1, now=self.now)  # 92 -> 93 -> 73

        self.assertEqual(first.statistics.normal_movement_total, 1)
        self.assertEqual(first.statistics.actual_position_change_total, 38)
        self.assertEqual(first.statistics.ladder_distance_total, 37)
        self.assertEqual(second.statistics.normal_movement_total, 1)
        self.assertEqual(second.statistics.actual_position_change_total, 19)
        self.assertEqual(second.statistics.snake_distance_total, 20)

    def test_same_positions_share_provisional_rank(self) -> None:
        game = GameState.create(self.board, [player(1, 40), player(2, 40), player(3, 20)])
        self.assertEqual(game.rankings(), {"p1": 1, "p2": 1, "p3": 3})

    def test_pause_suspend_resume_and_abort_are_distinct(self) -> None:
        game = GameState.create(self.board, [player(1), player(2)])
        game.start(now=self.now)
        game.pause()
        self.assertEqual(game.status, GameStatus.PAUSED)
        game.suspend(now=self.now)
        self.assertEqual(game.status, GameStatus.SUSPENDED)
        game.resume()
        self.assertEqual(game.status, GameStatus.IN_PROGRESS)
        game.abort(now=self.now)
        self.assertEqual(game.status, GameStatus.ABORTED)
        self.assertIsNone(game.current_player)
        self.assertFalse(any(item.rank_confirmed for item in game.players))


if __name__ == "__main__":
    unittest.main()

