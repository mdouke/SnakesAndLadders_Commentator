from __future__ import annotations

from commentator.domain.game import GameState, PlayerState
from commentator.presets import load_board_preset


def game() -> GameState:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "アオイ", "red"), PlayerState("b", "ハル", "blue"),
    ])
    state.start()
    return state


def test_timeline_lists_operations_in_history_order() -> None:
    state = game()
    first = state.roll(3)
    state.correct_position("b", 5, reason="盤とずれた", operator="admin")
    state.change_turn("a", reason="順番", operator="admin")
    state.pause()
    timeline = state.to_dict()["timeline"]

    assert [entry["type"] for entry in timeline] == [
        "start", "roll", "position_correction", "turn_change", "pause",
    ]
    assert timeline[1]["turnId"] == first.id
    assert timeline[2]["position"] == 5 and timeline[2]["beforePosition"] == 0
    assert timeline[2]["reason"] == "盤とずれた"
    assert timeline[3]["previousPlayerId"] == "b" and timeline[3]["playerId"] == "a"


def test_undone_operations_leave_the_timeline_but_stay_in_the_audit_log() -> None:
    state = game()
    state.roll(3)
    state.correct_position("b", 5, reason="盤とずれた", operator="admin")
    state.undo()
    data = state.to_dict()

    assert "position_correction" not in [entry["type"] for entry in data["timeline"]]
    assert data["auditLog"][0]["type"] == "undo"
    assert data["auditLog"][0]["label"].startswith("位置訂正")


def test_roll_correction_records_who_and_why_on_the_turn_and_audit_log() -> None:
    state = game()
    turn = state.roll(3)
    state.correct_roll(turn.id, 5, reason="誤入力", operator="admin")
    data = state.to_dict()

    corrected = data["turns"][0]["correctedFrom"]
    assert (corrected["value"], corrected["reason"], corrected["operator"]) == (
        3, "誤入力", "admin",
    )
    assert data["auditLog"][0]["label"] == "出目訂正（アオイ・ターン1: 3→5）"


def test_before_values_follow_a_corrected_earlier_roll() -> None:
    state = game()
    first = state.roll(3)                      # A 0 -> 3
    state.correct_position("a", 10, reason="盤とずれた", operator="admin")
    state.correct_roll(first.id, 5, reason="誤入力", operator="admin")   # A 0 -> 5
    entry = next(
        item for item in state.to_dict()["timeline"] if item["type"] == "position_correction"
    )
    assert entry["beforePosition"] == 5
