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

    state.resume_after_turn(second.id, reason="戻す", operator="admin")
    assert [turn.roll for turn in state.turns] == [3, 4]
    assert state.current_player is not None and state.current_player.id == "a"
    assert state.undo_history[-1].label == "ターン1の後から再開（ハル）"  # round 1


def test_resume_keeps_a_correction_made_after_that_turn() -> None:
    state = game()
    first = state.roll(3)
    state.roll(4)
    state.roll(5)
    state.correct_roll(first.id, 6, reason="誤入力", operator="admin")
    second = state.turns[1]

    state.resume_after_turn(second.id, reason="戻す", operator="admin")
    # A version snapshot from before the correction would bring back the 3.
    assert [turn.roll for turn in state.turns] == [6, 4]


def test_resume_can_be_undone_and_redone() -> None:
    state = game()
    first = state.roll(3)
    state.roll(4)
    state.resume_after_turn(first.id, reason="戻す", operator="admin")
    state.undo()
    assert [turn.roll for turn in state.turns] == [3, 4]
    state.redo()
    assert [turn.roll for turn in state.turns] == [3]


def test_resume_before_first_roll_and_latest_turn() -> None:
    state = game()
    state.roll(3)
    latest = state.roll(4)
    with pytest.raises(ValueError, match="最新"):
        state.resume_after_turn(latest.id, reason="戻す", operator="admin")
    state.resume_after_turn(None, reason="戻す", operator="admin")
    assert state.turns == []
    assert state.status is GameStatus.PAUSED  # waits for the admin to resume
    with pytest.raises(ValueError, match="まだ出目"):
        state.resume_after_turn(None, reason="戻す", operator="admin")


def test_resume_from_turn_api_refuses_aborted_game_until_reopened() -> None:
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

    body = {
        "turnId": first_turn, "expectedStateVersion": aborted["stateVersion"],
        "reason": "誤操作", "operator": "admin",
    }
    refused = request(app, "POST", f"/api/games/{created['id']}/resume-from-turn", body)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "game_aborted"

    reopened = request(app, "POST", f"/api/games/{created['id']}/reopen", {
        "expectedStateVersion": aborted["stateVersion"],
    }).json()
    body["expectedStateVersion"] = reopened["stateVersion"]
    resumed = request(app, "POST", f"/api/games/{created['id']}/resume-from-turn", body)
    assert resumed.status_code == 200
    assert [turn["roll"] for turn in resumed.json()["turns"]] == [3]
    assert resumed.json()["status"] == "paused"
    assert resumed.json()["pauseReason"] == "after_correction"


def test_resume_requires_who_and_why_and_is_audited() -> None:
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
    body: dict[str, object] = {
        "turnId": current["turns"][0]["id"], "expectedStateVersion": current["stateVersion"],
    }
    missing = request(app, "POST", f"/api/games/{created['id']}/resume-from-turn", body)
    assert missing.status_code == 422

    body.update(reason="振り直し", operator="admin")
    resumed = request(app, "POST", f"/api/games/{created['id']}/resume-from-turn", body).json()
    entry = resumed["auditLog"][0]
    assert entry["type"] == "restore"
    assert entry["label"] == "ターン1の後から再開（A）"
    assert (entry["reason"], entry["operator"]) == ("振り直し", "admin")
