from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from commentator.main import create_app
from commentator.presets import BoardPresetStore, repository_root

from .test_turn_change import request

LADDER_BOARD = {
    "id": "my-board",
    "name": "練習用",
    "goal": 100,
    "jumps": [
        {"type": "ladder", "from": 4, "to": 30},
        {"type": "snake", "from": 50, "to": 10},
    ],
}


@pytest.fixture
def app(tmp_path: Path) -> Any:
    store = BoardPresetStore(repository_root() / "data" / "boards", tmp_path / "boards")
    return create_app(board_store=store)


def test_list_includes_standard_board_as_built_in(app: Any) -> None:
    presets = request(app, "GET", "/api/boards").json()["presets"]
    standard = next(item for item in presets if item["id"] == "standard")
    assert standard["builtIn"] is True
    assert standard["snakeCount"] > 0 and standard["ladderCount"] > 0


def test_save_load_and_delete_user_board(app: Any, tmp_path: Path) -> None:
    saved = request(app, "PUT", "/api/boards/my-board", LADDER_BOARD)
    assert saved.status_code == 200
    assert (tmp_path / "boards" / "my-board.json").is_file()

    loaded = request(app, "GET", "/api/boards/my-board").json()
    assert loaded["jumps"] == LADDER_BOARD["jumps"]
    listed = request(app, "GET", "/api/boards").json()["presets"]
    assert {"id": "my-board", "builtIn": False}.items() <= next(
        item for item in listed if item["id"] == "my-board"
    ).items()

    assert request(app, "DELETE", "/api/boards/my-board").status_code == 204
    assert request(app, "GET", "/api/boards/my-board").status_code == 404


@pytest.mark.parametrize(
    ("jumps", "message"),
    [
        ([{"type": "ladder", "from": 4, "to": 30}, {"type": "snake", "from": 30, "to": 2}],
         "別の開始マス"),
        ([{"type": "snake", "from": 10, "to": 20}], "下のマス"),
        ([{"type": "ladder", "from": 4, "to": 30}, {"type": "ladder", "from": 4, "to": 40}],
         "重複"),
        ([{"type": "ladder", "from": 0, "to": 30}], "範囲外"),
    ],
)
def test_validate_reports_the_domain_rule(
    app: Any, jumps: list[dict[str, Any]], message: str
) -> None:
    result = request(app, "POST", "/api/boards/validate", {**LADDER_BOARD, "jumps": jumps})
    assert result.status_code == 200
    assert result.json()["valid"] is False
    assert message in result.json()["message"]

    saved = request(app, "PUT", "/api/boards/my-board", {**LADDER_BOARD, "jumps": jumps})
    assert saved.status_code == 422
    assert saved.json()["detail"]["code"] == "invalid_board"


def test_built_in_board_cannot_be_overwritten_or_deleted(app: Any) -> None:
    body = {**LADDER_BOARD, "id": "standard"}
    assert request(app, "PUT", "/api/boards/standard", body).json()["detail"]["code"] == (
        "board_preset_read_only"
    )
    assert request(app, "DELETE", "/api/boards/standard").status_code == 409


def test_unsafe_ids_and_mismatched_path_are_rejected(app: Any) -> None:
    body = {**LADDER_BOARD, "id": "../evil"}
    assert request(app, "PUT", "/api/boards/..%2Fevil", body).status_code in {404, 422}
    assert request(app, "PUT", "/api/boards/other", LADDER_BOARD).status_code == 422
    assert request(app, "GET", "/api/boards/Bad_ID").status_code == 422


def test_game_uses_the_selected_board(app: Any) -> None:
    request(app, "PUT", "/api/boards/my-board", LADDER_BOARD)
    players = [{"name": "A", "color": "red"}, {"name": "B", "color": "blue"}]
    game = request(app, "POST", "/api/games", {"players": players, "boardId": "my-board"}).json()
    assert game["boardId"] == "my-board"
    assert game["board"]["jumps"] == LADDER_BOARD["jumps"]

    game = request(app, "POST", f"/api/games/{game['id']}/start", {"expectedStateVersion": 0})
    rolled = request(app, "POST", f"/api/games/{game.json()['id']}/rolls", {
        "value": 4, "expectedStateVersion": game.json()["stateVersion"],
    }).json()
    assert rolled["turns"][0]["finalPosition"] == 30  # took the custom ladder

    missing = request(app, "POST", "/api/games", {"players": players, "boardId": "nope"})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "board_not_found"
