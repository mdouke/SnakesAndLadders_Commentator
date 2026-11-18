from __future__ import annotations

from pathlib import Path

import pytest
from commentator.domain.game import GameState, GameStatus, PlayerState
from commentator.infrastructure.persistence import SQLiteGameRepository
from commentator.presets import load_board_preset


def game() -> GameState:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "アオイ", "red"),
        PlayerState("b", "ハル", "blue"),
        PlayerState("c", "ソラ", "green"),
    ])
    state.start()
    return state


def test_abort_is_not_undoable_and_blocks_changes() -> None:
    state = game()
    state.roll(3)
    state.abort()
    assert state.undo_history[-1].label == "アオイの出目 3（ターン1）"
    for action in (state.undo, state.redo):
        with pytest.raises(ValueError, match="中止を取り消して"):
            action()
    with pytest.raises(ValueError, match="中止を取り消して"):
        state.correct_roll(state.turns[0].id, 4, reason="x", operator="y")


def test_reopen_restores_status_and_turn_including_turn_change() -> None:
    state = game()
    state.roll(3)                                   # A -> next B
    state.change_turn("c", reason="順番", operator="admin")  # also pauses
    state.abort()
    state.reopen()
    assert state.status is GameStatus.PAUSED
    assert state.current_player is not None and state.current_player.id == "c"
    assert state.ended_at is None


def test_undo_after_reopen_keeps_turn_order_consistent() -> None:
    state = game()
    state.roll(3)          # A rolled, B is next
    state.abort()
    state.reopen()
    state.undo()           # undo A's roll (Undo pauses the game)
    assert state.turns == []
    assert state.status is GameStatus.PAUSED
    assert state.current_player is not None and state.current_player.id == "a"


def test_reopen_works_after_loading_from_sqlite(tmp_path: Path) -> None:
    repository = SQLiteGameRepository(tmp_path / "games.sqlite3")
    state = game()
    state.roll(5)
    state.abort()
    repository.save(state)

    loaded = SQLiteGameRepository(tmp_path / "games.sqlite3").get(state.id)
    assert loaded is not None
    loaded.reopen()
    assert loaded.status is GameStatus.IN_PROGRESS
    assert loaded.current_player is not None and loaded.current_player.id == "b"


def test_reopen_a_game_aborted_before_start_returns_to_ready() -> None:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"), PlayerState("b", "B", "blue"),
    ])
    state.abort()
    state.reopen()
    assert state.status is GameStatus.READY
    with pytest.raises(ValueError, match="中止したゲームだけ"):
        state.reopen()
