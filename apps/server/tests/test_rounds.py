from __future__ import annotations

from pathlib import Path

from commentator.domain.game import GameState, PlayerState
from commentator.infrastructure.persistence import SQLiteGameRepository
from commentator.infrastructure.persistence.sqlite import game_from_snapshot, game_to_snapshot
from commentator.presets import load_board_preset


def game(players: int = 3, **positions: int) -> GameState:
    ids = "abc"[:players]
    state = GameState.create(load_board_preset(), [
        PlayerState(item, item.upper(), color, position=positions.get(item, 0))
        for item, color in zip(ids, ("red", "blue", "green"), strict=False)
    ])
    state.start()
    return state


def test_round_advances_after_everyone_has_rolled_once() -> None:
    state = game()
    assert state.round_number == 1
    for value in (1, 1, 1, 1):
        state.roll(value)
    assert [turn.round for turn in state.turns] == [1, 1, 1, 2]
    assert [turn.number for turn in state.turns] == [1, 2, 3, 4]
    assert state.round_number == 2
    assert state.to_dict()["roundNumber"] == 2


def test_finished_player_is_skipped_without_breaking_rounds() -> None:
    state = game(a=99)
    state.roll(1)          # A reaches the goal in round 1
    state.roll(1)          # B
    state.roll(1)          # C, wraps to B
    state.roll(1)          # B, round 2
    assert [turn.round for turn in state.turns] == [1, 1, 1, 2]


def test_turn_change_does_not_start_a_new_round_by_itself() -> None:
    state = game()
    state.change_turn("c", reason="順番", operator="admin")
    state.resume()
    state.roll(1)          # C in round 1, next is A -> round 2
    assert state.turns[0].round == 1
    assert state.round_number == 2


def test_undo_recomputes_the_round() -> None:
    state = game(players=2)
    state.roll(1)
    state.roll(1)
    assert state.round_number == 2
    state.undo()
    assert state.round_number == 1


def test_rounds_survive_sqlite_and_old_snapshots_are_migrated(tmp_path: Path) -> None:
    state = game(players=2)
    for value in (3, 4, 5):
        state.roll(value)
    repository = SQLiteGameRepository(tmp_path / "games.sqlite3")
    repository.save(state)
    loaded = SQLiteGameRepository(tmp_path / "games.sqlite3").get(state.id)
    assert loaded is not None
    assert [turn.round for turn in loaded.turns] == [1, 1, 2]

    legacy = game_to_snapshot(state)
    del legacy["roundNumber"]
    for turn in legacy["turns"]:
        del turn["round"]
    migrated = game_from_snapshot(legacy)
    assert [turn.round for turn in migrated.turns] == [1, 1, 2]
    assert migrated.round_number == 2
