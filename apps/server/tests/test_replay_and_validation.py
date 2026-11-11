from datetime import UTC, datetime

import pytest
from commentator.domain.board import Board, Jump, JumpType
from commentator.domain.game import GameState, PlayerState, RollInput, compute_statistics, replay
from commentator.presets import load_board_preset


def test_replay_is_deterministic_and_does_not_change_initial() -> None:
    initial = GameState.create(
        load_board_preset(),
        [PlayerState("a", "A", "red"), PlayerState("b", "B", "blue")],
        game_id="fixed",
    )
    now = datetime(2026, 11, 11, tzinfo=UTC)
    rolls = [RollInput(value, now) for value in [1, 4, 6, 2]]
    first = replay(initial, rolls, started_at=now)
    second = replay(initial, rolls, started_at=now)
    assert first.to_dict() == second.to_dict()
    assert initial.turn_number == 0
    assert initial.players[0].position == 0
    prefix = replay(initial, rolls[:2], started_at=now)
    corrected = replay(initial, [RollInput(2, now), *rolls[1:]], started_at=now)
    assert prefix.players[0].statistics.rolls == [1]
    assert corrected.players[0].statistics.rolls == [2, 6]
    assert first.players[0].statistics.rolls == [1, 6]
    assert compute_statistics(first.turns[:1]).to_dict() == prefix.players[0].statistics.to_dict()


def test_winning_rolls_include_ladder_and_exact_finish() -> None:
    board = load_board_preset()
    assert board.winning_rolls(75) == [5]
    assert board.winning_rolls(99) == [1]
    assert board.winning_rolls(100) == []
    assert board.winning_rolls(0) == []


def test_rejects_board_with_six_square_snake_barrier() -> None:
    with pytest.raises(ValueError, match="ゴールへ到達できない"):
        Board("trap", "Trap", 100, tuple(
            Jump(JumpType.SNAKE, square, 1) for square in range(50, 56)
        ))
