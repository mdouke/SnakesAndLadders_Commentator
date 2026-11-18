from __future__ import annotations

import pytest
from commentator.domain.game import GameState, GameStatus, PlayerState
from commentator.main import create_app
from commentator.presets import load_board_preset

from .test_turn_change import request


def game() -> GameState:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "アオイ", "red"), PlayerState("b", "ハル", "blue"),
    ])
    state.start()
    return state


def test_resume_after_turn_drops_later_turns() -> None:
    state = game()
    state.roll(3)
    second = state.roll(4)
    state.roll(5)
    state.roll(6)

    state.resume_after_turn(second.id)
    assert [turn.roll for turn in state.turns] == [3, 4]
    assert state.current_player is not None and state.current_player.id == "a"
    assert state.undo_history[-1].label == "ターン2の後から再開（ハル）"


def test_resume_keeps_a_correction_made_after_that_turn() -> None:
    state = game()
    first = state.roll(3)
    state.roll(4)
    state.roll(5)
    state.correct_roll(first.id, 6, reason="誤入力", operator="admin")
    second = state.turns[1]

    state.resume_after_turn(second.id)
    # A version snapshot from before the correction would bring back the 3.
    assert [turn.roll for turn in state.turns] == [6, 4]


def test_resume_can_be_undone_and_redone() -> None:
    state = game()
    first = state.roll(3)
    state.roll(4)
    state.resume_after_turn(first.id)
    state.undo()
    assert [turn.roll for turn in state.turns] == [3, 4]
    state.redo()
    assert [turn.roll for turn in state.turns] == [3]


def test_resume_before_first_roll_and_latest_turn() -> None:
    state = game()
    state.roll(3)
    latest = state.roll(4)
    with pytest.raises(ValueError, match="最新"):
        state.resume_after_turn(latest.id)
    state.resume_after_turn(None)
    assert state.turns == []
    assert state.status is GameStatus.IN_PROGRESS
    with pytest.raises(ValueError, match="まだ出目"):
        state.resume_after_turn(None)


def test_resume_from_turn_api_requires_confirmation_for_ended_game() -> None:
    app = create_app()
    players = {"players": [{"name": "A", "color": "red"}, {"name": "B", "color": "blue"}]}
    created = request(app, "POST", "/api/games", players).json()
    current = request(
        app, "POST", f"/api/games/{created['id']}/start", {"expectedStateVersion": 0}
    ).json()
    for value in (3, 4):
        current = request(app, "POST", f"/api/games/{created['id']}/rolls", {
            "value": value, "expectedStateVersion": current["stateVersion"],
        }).json()
    first_turn = current["turns"][0]["id"]
    aborted = request(app, "POST", f"/api/games/{created['id']}/abort", {
        "expectedStateVersion": current["stateVersion"],
    }).json()

    body = {"turnId": first_turn, "expectedStateVersion": aborted["stateVersion"]}
    refused = request(app, "POST", f"/api/games/{created['id']}/resume-from-turn", body)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "ended_game_confirmation_required"

    body["confirmEndedGame"] = True
    resumed = request(app, "POST", f"/api/games/{created['id']}/resume-from-turn", body)
    assert resumed.status_code == 200
    assert [turn["roll"] for turn in resumed.json()["turns"]] == [3]
    assert resumed.json()["status"] == "in_progress"
