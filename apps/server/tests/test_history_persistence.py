from __future__ import annotations

import asyncio
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from commentator.domain.game import (
    CorrectionConfirmationRequired,
    GameState,
    GameStatus,
    OperationInput,
    OperationType,
    PlayerState,
    replay_operations,
)
from commentator.infrastructure.persistence import SQLiteGameRepository
from commentator.main import MemoryGameRepository, create_app, recover_interrupted_games
from commentator.presets import load_board_preset
from httpx import ASGITransport, AsyncClient

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def game() -> GameState:
    return GameState.create(
        load_board_preset(),
        [PlayerState("a", "A", "red"), PlayerState("b", "B", "blue")],
        game_id="history-game",
    )


def test_all_game_operations_can_be_replayed() -> None:
    state = replay_operations(game(), [
        OperationInput(OperationType.START, NOW),
        OperationInput(OperationType.ROLL, NOW, {"value": 1}),
        OperationInput(OperationType.PAUSE, NOW),
        OperationInput(
            OperationType.POSITION_CORRECTION,
            NOW,
            {"playerId": "a", "position": 12},
        ),
        OperationInput(OperationType.RESUME, NOW),
        OperationInput(OperationType.SUSPEND, NOW),
        OperationInput(OperationType.RESUME, NOW),
        OperationInput(OperationType.ABORT, NOW),
    ])
    assert state.status is GameStatus.ABORTED
    assert state.players[0].position == 12
    assert state.turns[0].roll == 1
    assert state.state_version == 8


def test_correction_preserves_prefix_turn_ids_and_undo_redo() -> None:
    state = game()
    state.start(now=NOW)
    for value in [1, 2, 3]:
        state.roll(value, now=NOW)
    before = [turn.id for turn in state.turns]

    state.correct_roll(
        state.turns[1].id, 4, reason="誤入力", operator="test", branch_id="branch-2", now=NOW
    )
    after = [turn.id for turn in state.turns]
    assert after[0] == before[0]
    assert after[1:] != before[1:]
    assert state.turns[1].roll == 4
    assert any(not event.active for event in state.operation_events)

    state.undo(now=NOW)
    assert [turn.roll for turn in state.turns] == [1, 2, 3]
    assert [turn.id for turn in state.turns] == before
    state.undo(now=NOW)
    assert [turn.roll for turn in state.turns] == [1, 2]
    state.redo(now=NOW)
    assert [turn.roll for turn in state.turns] == [1, 2, 3]
    state.redo(now=NOW)
    assert [turn.roll for turn in state.turns] == [1, 4, 3]
    assert [turn.id for turn in state.turns] == after


def test_sqlite_restores_state_events_turns_and_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "games.sqlite3"
    repository = SQLiteGameRepository(path)
    state = game()
    repository.save(state)
    state.start(now=NOW)
    repository.save(state)
    state.roll(1, now=NOW)
    repository.save(state)

    restored = SQLiteGameRepository(path).get(state.id)
    assert restored is not None
    assert restored.to_dict() == state.to_dict()
    assert len(restored.operation_events) == 2
    assert SQLiteGameRepository(path).snapshot(state.id, 1) is not None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM games").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM operation_events").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM turns").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 3

    snapshot = repository.snapshot(state.id, 1)
    assert snapshot is not None
    state.restore_from(snapshot, now=NOW)
    repository.save(state)
    restored_again = SQLiteGameRepository(path).get(state.id)
    assert restored_again is not None
    assert restored_again.turn_number == 0
    assert restored_again.state_version == 3
    assert any(event.type is OperationType.RESTORE for event in restored_again.operation_events)


class FailingRepository(MemoryGameRepository):
    fail: bool = False

    def save(self, state: GameState) -> None:
        if self.fail:
            raise sqlite3.OperationalError("disk full")
        super().save(state)


def test_stale_version_and_failed_save_do_not_publish_changed_state() -> None:
    async def scenario() -> None:
        repository = FailingRepository()
        async with AsyncClient(
            transport=ASGITransport(app=create_app(repository=repository)),
            base_url="http://test",
        ) as client:
            created = (await client.post("/api/games", json={"players": [
                {"name": "A", "color": "red"}, {"name": "B", "color": "blue"},
            ]})).json()
            path = f"/api/games/{created['id']}"
            started = await client.post(path + "/start", json={"expectedStateVersion": 0})
            assert started.status_code == 200
            stale = await client.post(path + "/pause", json={"expectedStateVersion": 0})
            assert stale.status_code == 409
            repository.fail = True
            failed = await client.post(path + "/pause", json={"expectedStateVersion": 1})
            assert failed.status_code == 503
            stored = repository.current()
            assert stored is not None
            assert stored.status is GameStatus.IN_PROGRESS
            assert stored.state_version == 1

    asyncio.run(scenario())


def test_undo_then_new_roll_uses_new_branch_and_preserves_old_turn(tmp_path: Path) -> None:
    repository = SQLiteGameRepository(tmp_path / "branch.sqlite3")
    state = game()
    state.start(now=NOW)
    original = state.roll(3, now=NOW)
    repository.save(state)
    state.undo(now=NOW)
    replacement = state.roll(5, now=NOW)
    repository.save(state)

    assert replacement.id != original.id
    assert not state.redo_history
    with sqlite3.connect(repository.path) as connection:
        rows = connection.execute(
            "SELECT id, active FROM turns ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert sorted(active for _, active in rows) == [0, 1]


def test_rebuild_keeps_turn_versions_and_multilevel_undo_redo() -> None:
    state = game()
    state.start(now=NOW)
    for value in [1, 2, 3]:
        state.roll(value, now=NOW)
    assert [
        (turn.state_version_before, turn.state_version_after) for turn in state.turns
    ] == [(1, 2), (2, 3), (3, 4)]
    state.undo(now=NOW)
    assert [turn.roll for turn in state.turns] == [1, 2]
    assert [
        (turn.state_version_before, turn.state_version_after) for turn in state.turns
    ] == [(1, 2), (2, 3)]
    state.undo(now=NOW)
    assert [turn.roll for turn in state.turns] == [1]
    state.redo(now=NOW)
    state.redo(now=NOW)
    assert [turn.roll for turn in state.turns] == [1, 2, 3]


def test_roll_correction_skips_finished_player_and_preserves_other_rolls() -> None:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"),
        PlayerState("b", "B", "blue"),
        PlayerState("c", "C", "green"),
    ])
    state.start(now=NOW)
    state.correct_position("a", 79, reason="setup", operator="test", now=NOW)
    for value in [2, 3, 4, 5, 6]:
        state.roll(value, now=NOW)
    target = state.turns[0].id
    before = state.to_dict()
    with pytest.raises(CorrectionConfirmationRequired):
        state.correct_roll(target, 1, reason="fix", operator="test", now=NOW)
    assert state.to_dict() == before
    state.correct_roll(
        target, 1, reason="fix", operator="test", now=NOW,
        confirm_invalidated_rolls=True,
    )
    assert [turn.roll for turn in state.turns] == [1, 3, 4, 6]
    assert [turn.player_id for turn in state.turns] == ["a", "b", "c", "b"]
    assert state.current_player is not None and state.current_player.id == "c"
    correction = next(
        event for event in reversed(state.operation_events)
        if event.type is OperationType.CORRECTION
    )
    assert correction.payload["invalidatedRolls"][0]["reason"] == "player_already_finished"
    state.undo(now=NOW)
    assert [turn.roll for turn in state.turns] == [2, 3, 4, 5, 6]

    two_players = game()
    two_players.start(now=NOW)
    two_players.correct_position("a", 79, reason="setup", operator="test", now=NOW)
    first_turn = two_players.roll(2, now=NOW).id
    two_players.roll(3, now=NOW)
    two_players.correct_roll(
        first_turn, 1, reason="fix", operator="test", now=NOW,
        confirm_invalidated_rolls=True,
    )
    assert two_players.status is GameStatus.COMPLETED
    assert [turn.roll for turn in two_players.turns] == [1]
    two_players.undo(now=NOW)
    assert [turn.roll for turn in two_players.turns] == [2, 3]


@pytest.mark.parametrize("interruption", ["pause", "suspend"])
def test_roll_correction_invalidates_operations_after_completion_and_undoes(
    interruption: str,
) -> None:
    state = game()
    state.start(now=NOW)
    state.correct_position("a", 79, reason="setup", operator="test", now=NOW)
    target_turn = state.roll(2, now=NOW).id
    state.roll(3, now=NOW)
    if interruption == "pause":
        state.pause(now=NOW)
    else:
        state.suspend(now=NOW)
    state.resume(now=NOW)
    state.roll(4, now=NOW)
    original_turns = [turn.to_dict() for turn in state.turns]
    original_types = [event.type for event in state._active_timeline()]

    with pytest.raises(CorrectionConfirmationRequired) as required:
        state.correct_roll(target_turn, 1, reason="fix", operator="test", now=NOW)
    assert [item["value"] for item in required.value.invalidated_turns] == [3, 4]
    assert [item["type"] for item in required.value.invalidated_operations] == [
        interruption, "resume",
    ]

    state.correct_roll(
        target_turn, 1, reason="fix", operator="test", now=NOW,
        confirm_invalidated_rolls=True,
    )
    assert state.status is GameStatus.COMPLETED
    assert [turn.roll for turn in state.turns] == [1]
    correction = next(
        event for event in reversed(state.operation_events)
        if event.type is OperationType.CORRECTION
    )
    assert [item["type"] for item in correction.payload["invalidatedOperations"]] == [
        interruption, "resume",
    ]

    state.undo(now=NOW)
    assert [turn.to_dict() for turn in state.turns] == original_turns
    assert [event.type for event in state._active_timeline()] == original_types
    assert state.to_dict()["status"] == GameStatus.IN_PROGRESS.value


def test_roll_correction_invalidates_position_correction_for_finished_player() -> None:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red"),
        PlayerState("b", "B", "blue"),
        PlayerState("c", "C", "green"),
    ])
    state.start(now=NOW)
    state.correct_position("a", 79, reason="setup", operator="test", now=NOW)
    target_turn = state.roll(2, now=NOW).id
    state.roll(3, now=NOW)
    state.correct_position("a", 50, reason="physical board", operator="test", now=NOW)

    with pytest.raises(CorrectionConfirmationRequired) as required:
        state.correct_roll(target_turn, 1, reason="fix", operator="test", now=NOW)
    assert required.value.invalidated_operations == [{
        "eventId": required.value.invalidated_operations[0]["eventId"],
        "timelineIndex": required.value.invalidated_operations[0]["timelineIndex"],
        "type": "position_correction",
        "playerId": "a",
        "position": 50,
        "reason": "player_already_finished",
    }]


def test_roll_correction_rejects_cancelled_goal_but_normal_change_succeeds() -> None:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red", position=99),
        PlayerState("b", "B", "blue"),
        PlayerState("c", "C", "green"),
    ])
    state.start(now=NOW)
    goal_turn = state.roll(1, now=NOW).id
    state.roll(2, now=NOW)
    before = state.to_dict()
    with pytest.raises(ValueError, match="後続のターンをUndo"):
        state.correct_roll(goal_turn, 2, reason="fix", operator="test", now=NOW)
    assert state.to_dict() == before

    ordinary = game()
    ordinary.start(now=NOW)
    turn = ordinary.roll(2, now=NOW)
    ordinary.correct_roll(turn.id, 3, reason="fix", operator="test", now=NOW)
    assert [item.roll for item in ordinary.turns] == [3]


def test_position_correction_rejects_goal_and_is_undoable() -> None:
    state = game()
    state.start(now=NOW)
    with pytest.raises(ValueError, match="ゴールに移動できません"):
        state.correct_position("a", 100, reason="bad", operator="test", now=NOW)
    state.correct_position("a", 20, reason="physical board", operator="test", now=NOW)
    assert state.players[0].position == 20
    state.undo(now=NOW)
    assert state.players[0].position == 0


def test_position_correction_recalculates_goal_ranks_and_terminal_status() -> None:
    state = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red", position=99),
        PlayerState("b", "B", "blue", position=99),
        PlayerState("c", "C", "green"),
        PlayerState("d", "D", "yellow"),
    ])
    state.start(now=NOW)
    state.roll(1, now=NOW)
    state.roll(1, now=NOW)
    state.correct_position("a", 50, reason="wrong goal", operator="test", now=NOW)
    assert state.status is GameStatus.IN_PROGRESS
    assert [player.rank for player in state.players] == [2, 1, 3, 3]

    aborted = game()
    aborted.start(now=NOW)
    aborted.roll(3, now=NOW)
    aborted.abort(now=NOW)
    aborted.correct_position("a", 10, reason="physical board", operator="test", now=NOW)
    assert aborted.status is GameStatus.ABORTED
    assert aborted.current_player is None

    completed = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red", position=99),
        PlayerState("b", "B", "blue"),
    ])
    completed.start(now=NOW)
    completed.roll(1, now=NOW)
    completed.correct_position("b", 25, reason="last place", operator="test", now=NOW)
    assert completed.status is GameStatus.COMPLETED
    assert completed.current_player is None

    reopened = GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red", position=99),
        PlayerState("b", "B", "blue", position=99),
        PlayerState("c", "C", "green"),
    ])
    reopened.start(now=NOW)
    reopened.roll(1, now=NOW)
    reopened.roll(1, now=NOW)
    reopened.correct_position("a", 50, reason="wrong goal", operator="test", now=NOW)
    assert reopened.status is GameStatus.IN_PROGRESS
    assert reopened.current_player is not None
    assert reopened.current_player.id == "c"


def test_statistics_histories_are_derived_after_sqlite_restore(tmp_path: Path) -> None:
    repository = SQLiteGameRepository(tmp_path / "statistics.sqlite3")
    state = game()
    state.start(now=NOW)
    state.roll(1, now=NOW)
    repository.save(state)
    restored = repository.get(state.id)
    assert restored is not None
    assert len(restored.players[0].statistics.ladder_history) == 1
    assert restored.players[0].statistics.ladder_history[0].end == 38


def test_restore_and_undo_redo_survive_sqlite_reload(tmp_path: Path) -> None:
    repository = SQLiteGameRepository(tmp_path / "undo.sqlite3")
    state = game()
    repository.save(state)
    state.start(now=NOW)
    state.roll(1, now=NOW)
    repository.save(state)
    snapshot = deepcopy(state)
    state.roll(2, now=NOW)
    state.restore_from(snapshot, now=NOW)
    assert [turn.roll for turn in state.turns] == [1]
    state.undo(now=NOW)
    assert [turn.roll for turn in state.turns] == [1, 2]
    repository.save(state)

    restored = repository.get(state.id)
    assert restored is not None
    restored.redo(now=NOW)
    assert [turn.roll for turn in restored.turns] == [1]


def test_current_game_is_explicit_and_recovery_pauses_only_in_progress() -> None:
    repository = MemoryGameRepository()
    first = game()
    repository.save(first)
    repository.set_current(first.id)
    second = GameState.create(
        load_board_preset(),
        [PlayerState("c", "C", "green"), PlayerState("d", "D", "yellow")],
        game_id="second",
    )
    repository.save(second)
    current = repository.current()
    assert current is not None
    assert current.id == first.id

    first.start(now=NOW)
    repository.save(first)
    second.start(now=NOW)
    second.pause(now=NOW)
    repository.save(second)
    recover_interrupted_games(repository)
    recovered = repository.get(first.id)
    unchanged = repository.get(second.id)
    assert recovered is not None and recovered.status is GameStatus.PAUSED
    assert recovered.interruption_detected_at is not None
    assert unchanged is not None and unchanged.status is GameStatus.PAUSED
    recovered.resume(now=NOW)
    assert recovered.interruption_detected_at is None


def test_app_lifespan_detects_interrupted_game() -> None:
    async def scenario() -> None:
        repository = MemoryGameRepository()
        state = game()
        repository.save(state)
        repository.set_current(state.id)
        state.start(now=NOW)
        repository.save(state)
        application = create_app(repository=repository)

        async with application.router.lifespan_context(application):
            recovered = repository.get(state.id)
            assert recovered is not None
            assert recovered.status is GameStatus.PAUSED
            assert recovered.interruption_detected_at is not None

    asyncio.run(scenario())


def test_ended_game_changes_require_confirmation() -> None:
    async def scenario() -> None:
        repository = MemoryGameRepository()
        async with AsyncClient(
            transport=ASGITransport(app=create_app(repository=repository)),
            base_url="http://test",
        ) as client:
            created = (await client.post("/api/games", json={"players": [
                {"name": "A", "color": "red"}, {"name": "B", "color": "blue"},
            ]})).json()
            path = f"/api/games/{created['id']}"
            await client.post(path + "/start", json={"expectedStateVersion": 0})
            aborted = (await client.post(
                path + "/abort", json={"expectedStateVersion": 1}
            )).json()
            denied = await client.post(
                path + "/undo", json={"expectedStateVersion": aborted["stateVersion"]}
            )
            assert denied.status_code == 409
            assert denied.json()["detail"]["code"] == "ended_game_confirmation_required"
            allowed = await client.post(path + "/undo", json={
                "expectedStateVersion": aborted["stateVersion"],
                "confirmEndedGame": True,
            })
            assert allowed.status_code == 200
            assert allowed.json()["status"] == "in_progress"

    asyncio.run(scenario())


def test_completed_game_roll_correction_cannot_cancel_goal() -> None:
    async def scenario() -> None:
        repository = MemoryGameRepository()
        state = GameState.create(load_board_preset(), [
            PlayerState("a", "A", "red", position=99),
            PlayerState("b", "B", "blue"),
        ], game_id="completed")
        state.start(now=NOW)
        turn = state.roll(1, now=NOW)
        repository.save(state)
        repository.set_current(state.id)
        async with AsyncClient(
            transport=ASGITransport(app=create_app(repository=repository)),
            base_url="http://test",
        ) as client:
            path = f"/api/games/{state.id}/turns/{turn.id}/correction"
            payload = {
                "expectedStateVersion": state.state_version,
                "value": 2, "reason": "goal roll was wrong", "operator": "tester",
            }
            assert (await client.post(path, json=payload)).status_code == 409
            payload["confirmEndedGame"] = True
            response = await client.post(path, json=payload)
            assert response.status_code == 409
            assert "後続のターンをUndo" in response.json()["detail"]["message"]

    asyncio.run(scenario())


def test_roll_correction_preview_and_confirmation_api() -> None:
    async def scenario() -> None:
        repository = MemoryGameRepository()
        state = GameState.create(load_board_preset(), [
            PlayerState("a", "A", "red"), PlayerState("b", "B", "blue"),
            PlayerState("c", "C", "green"),
        ], game_id="preview")
        state.start(now=NOW)
        state.correct_position("a", 79, reason="setup", operator="test", now=NOW)
        for value in [2, 3, 4, 5, 6]:
            state.roll(value, now=NOW)
        target = state.turns[0].id
        repository.save_and_set_current(state)
        payload = {
            "expectedStateVersion": state.state_version,
            "value": 1, "reason": "fix", "operator": "tester",
        }
        path = f"/api/games/{state.id}/turns/{target}/correction"
        async with AsyncClient(
            transport=ASGITransport(app=create_app(repository=repository)),
            base_url="http://test",
        ) as client:
            preview = await client.post(path, json={**payload, "dryRun": True})
            assert preview.status_code == 200
            assert preview.json()["requiresConfirmation"] is True
            assert preview.json()["invalidatedTurns"][0]["value"] == 5
            denied = await client.post(path, json=payload)
            assert denied.status_code == 409
            assert denied.json()["detail"]["code"] == (
                "roll_invalidation_confirmation_required"
            )
            accepted = await client.post(
                path, json={**payload, "confirmInvalidatedRolls": True}
            )
            assert accepted.status_code == 200
            assert [turn["roll"] for turn in accepted.json()["turns"]] == [1, 3, 4, 6]

    asyncio.run(scenario())
