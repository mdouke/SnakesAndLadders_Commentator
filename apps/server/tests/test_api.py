from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from commentator.main import create_app
from httpx import ASGITransport, AsyncClient, Response


@pytest.fixture
def api() -> Callable[..., Response]:
    app = create_app()

    def request(method: str, path: str, *, json: Any = None) -> Response:
        async def send() -> Response:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, json=json)

        return asyncio.run(send())

    return request


def test_health_and_default_board(api: Callable[..., Response]) -> None:
    assert api("GET", "/health").json() == {"status": "ok"}
    board = api("GET", "/api/boards/default")
    assert board.status_code == 200
    assert board.json()["goal"] == 100
    assert len(board.json()["jumps"]) == 19


def test_create_start_and_submit_manual_roll(api: Callable[..., Response]) -> None:
    created = api(
        "POST",
        "/api/games",
        json={
            "players": [
                {"name": "あおい", "color": "red", "spokenName": "あおい"},
                {"name": "たくみ", "color": "blue", "spokenName": "たくみ"},
            ]
        },
    )
    assert created.status_code == 201
    game = created.json()
    assert game["status"] == "ready"
    assert len(game["players"]) == 2

    started = api(
        "POST", f"/api/games/{game['id']}/start", json={"expectedStateVersion": 0}
    )
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"

    rolled = api(
        "POST", f"/api/games/{game['id']}/rolls",
        json={"value": 1, "expectedStateVersion": 1, "expectedTurnNumber": 0},
    )
    assert rolled.status_code == 200
    state = rolled.json()
    assert state["turnNumber"] == 1
    assert state["players"][0]["position"] == 38
    assert state["turns"][0]["normalPosition"] == 1
    assert state["turns"][0]["finalPosition"] == 38
    assert state["turns"][0]["jumpType"] == "ladder"


def test_rejects_game_with_fewer_than_two_players(api: Callable[..., Response]) -> None:
    response = api(
        "POST",
        "/api/games",
        json={"players": [{"name": "ひとり", "color": "red"}]},
    )
    assert response.status_code == 422


def test_concurrent_duplicate_rolls_only_advance_one_turn(api: Callable[..., Response]) -> None:
    game = api("POST", "/api/games", json={"players": [
        {"name": "A", "color": "red"}, {"name": "B", "color": "blue"}
    ]}).json()
    api("POST", f"/api/games/{game['id']}/start", json={"expectedStateVersion": 0})

    def roll() -> int:
        return api("POST", f"/api/games/{game['id']}/rolls",
                       json={"value": 1, "expectedStateVersion": 1,
                             "expectedTurnNumber": 0}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: roll(), range(2)))
    assert sorted(statuses) == [200, 409]
    assert api("GET", f"/api/games/{game['id']}").json()["turnNumber"] == 1

