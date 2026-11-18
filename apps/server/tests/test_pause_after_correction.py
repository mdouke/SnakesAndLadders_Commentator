from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
from commentator.domain.game import GameState, GameStatus, PlayerState
from commentator.infrastructure.persistence import SQLiteGameRepository
from commentator.presets import load_board_preset


def game() -> GameState:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"), PlayerState("b", "B", "blue"), PlayerState("c", "C", "green"),
    ])
    state.start()
    state.roll(3)
    state.roll(4)
    return state


CORRECTIONS: dict[str, Callable[[GameState], object]] = {
    "roll correction": lambda g: g.correct_roll(g.turns[0].id, 5, reason="r", operator="o"),
    "position correction": lambda g: g.correct_position("a", 10, reason="r", operator="o"),
    "turn change": lambda g: g.change_turn("a", reason="r", operator="o"),
    "rewind": lambda g: g.resume_after_turn(g.turns[0].id, reason="r", operator="o"),
    "undo": lambda g: g.undo(),
}


@pytest.mark.parametrize("name", CORRECTIONS)
def test_every_correction_leaves_the_game_paused(name: str) -> None:
    state = game()
    CORRECTIONS[name](state)
    assert state.status is GameStatus.PAUSED
    assert state.pause_reason == "after_correction"
    with pytest.raises(ValueError, match="進行中のゲームでのみ"):
        state.roll(1)
    state.resume()
    state.roll(1)


def test_redo_and_snapshot_restore_also_pause() -> None:
    state = game()
    state.undo()
    state.redo()  # still possible while paused
    assert state.pause_reason == "after_correction"
    assert len(state.turns) == 2
    state.undo()
    state.resume()  # a new operation, so Redo is no longer available
    assert not state.redo_history

    other = game()
    snapshot = deepcopy(other)  # what the repository keeps for this version
    other.roll(5)
    other.restore_from(snapshot)
    assert other.status is GameStatus.PAUSED
    assert len(other.turns) == 2


def test_manual_pause_reason_is_kept_and_completed_game_stays_completed() -> None:
    state = game()
    state.pause()
    state.correct_position("a", 10, reason="r", operator="o")
    assert state.pause_reason is None  # still the admin's own pause

    finished = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red", position=99), PlayerState("b", "B", "blue"),
    ])
    finished.start()
    finished.roll(1)
    finished.correct_position("b", 20, reason="r", operator="o")
    assert finished.status is GameStatus.COMPLETED


def test_history_with_holds_reloads_identically(tmp_path: Path) -> None:
    state = game()
    state.correct_position("a", 10, reason="r", operator="o")
    state.resume()
    state.roll(2)
    state.undo()
    repository = SQLiteGameRepository(tmp_path / "games.sqlite3")
    repository.save(state)
    loaded = repository.get(state.id)
    assert loaded is not None
    assert loaded.to_dict() == state.to_dict()
    loaded.resume_after_turn(loaded.turns[0].id, reason="r", operator="o")  # replays holds
    assert loaded.status is GameStatus.PAUSED
