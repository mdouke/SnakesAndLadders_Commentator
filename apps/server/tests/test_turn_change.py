from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from commentator.domain.game import GameState, PlayerState
from commentator.infrastructure.persistence import SQLiteGameRepository
from commentator.main import create_app
from commentator.presets import load_board_preset
from httpx import ASGITransport, AsyncClient


def three_players() -> GameState:
    game = GameState.create(load_board_preset(), [
        PlayerState("a", "アオイ", "red"),
        PlayerState("b", "ハル", "blue"),
        PlayerState("c", "ソラ", "green"),
    ])
    game.start()
    return game


def change(game: GameState, player_id: str) -> None:
    game.change_turn(player_id, reason="順番を間違えた", operator="admin")


def test_change_turn_moves_the_turn_and_next_rolls_continue_from_there() -> None:
    game = three_players()
    change(game, "c")
    assert game.current_player is not None and game.current_player.id == "c"
    assert game.status.value == "paused"  # corrections wait for the admin
    game.resume()

    game.roll(3)
    assert game.turns[-1].player_id == "c"
    assert game.current_player.id == "a"
    assert game.undo_history[1].label == "手番変更（アオイ→ソラ）"


def test_change_turn_rejects_invalid_targets() -> None:
    game = three_players()
    with pytest.raises(ValueError, match="すでに"):
        change(game, "a")
    with pytest.raises(ValueError, match="見つかりません"):
        change(game, "nobody")
    game.suspend()
    with pytest.raises(ValueError, match="進行中または一時停止中"):
        change(game, "b")


def test_change_turn_supports_undo_redo() -> None:
    game = three_players()
    change(game, "b")
    game.undo()
    assert game.current_player is not None and game.current_player.id == "a"
    game.redo()
    assert game.current_player is not None and game.current_player.id == "b"


def test_change_turn_survives_sqlite_reload(tmp_path: Path) -> None:
    repository = SQLiteGameRepository(tmp_path / "games.sqlite3")
    game = three_players()
    change(game, "c")
    game.resume()
    game.roll(2)
    repository.save(game)

    restored = SQLiteGameRepository(tmp_path / "games.sqlite3").get(game.id)
    assert restored is not None
    assert restored.to_dict() == game.to_dict()


def test_roll_correction_replays_across_a_turn_change() -> None:
    game = three_players()
    first = game.roll(3)          # A
    change(game, "c")             # skip B
    game.resume()
    game.roll(4)                  # C
    game.correct_roll(first.id, 5, reason="誤入力", operator="admin")

    assert [turn.player_id for turn in game.turns] == ["a", "c"]
    assert game.turns[0].roll == 5
    assert game.current_player is not None and game.current_player.id == "a"


def test_player_name_appears_in_undo_labels() -> None:
    game = three_players()
    turn = game.roll(3)
    assert game.undo_history[-1].label == "アオイの出目 3（ターン1）"
    game.correct_roll(turn.id, 4, reason="誤入力", operator="admin")
    assert game.undo_history[-1].label == "出目訂正（アオイ・ターン1: 3→4）"


def request(app: Any, method: str, path: str, json: Any = None) -> Any:
    async def send() -> Any:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            return await client.request(method, path, json=json)

    return asyncio.run(send())


def test_turn_change_api() -> None:
    app = create_app()
    players = {"players": [{"name": "A", "color": "red"}, {"name": "B", "color": "blue"}]}
    game = request(app, "POST", "/api/games", players).json()
    game = request(
        app, "POST", f"/api/games/{game['id']}/start", {"expectedStateVersion": 0}
    ).json()
    target = game["players"][1]["id"]
    body = {
        "expectedStateVersion": game["stateVersion"],
        "playerId": target,
        "reason": "順番を間違えた",
        "operator": "admin",
    }
    changed = request(app, "POST", f"/api/games/{game['id']}/turn-change", body)
    assert changed.status_code == 200
    assert changed.json()["currentPlayerId"] == target

    stale = request(app, "POST", f"/api/games/{game['id']}/turn-change", body)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "state_version_conflict"

    body["expectedStateVersion"] = changed.json()["stateVersion"]
    same = request(app, "POST", f"/api/games/{game['id']}/turn-change", body)
    assert same.status_code == 409
    assert same.json()["detail"]["code"] == "invalid_game_operation"
