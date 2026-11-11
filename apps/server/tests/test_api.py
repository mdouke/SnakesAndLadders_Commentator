from __future__ import annotations

import asyncio

from commentator.main import app
from httpx import ASGITransport, AsyncClient, Response


def request(method: str, path: str, **kwargs: object) -> Response:
    async def send() -> Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_health_and_default_board() -> None:
    assert request("GET", "/health").json() == {"status": "ok"}
    board = request("GET", "/api/boards/default")
    assert board.status_code == 200
    assert board.json()["goal"] == 100
    assert len(board.json()["jumps"]) == 19


def test_create_start_and_submit_manual_roll() -> None:
    created = request(
        "POST",
        "/api/games",
        json={
            "players": [
                {"name": "あおい", "color": "red", "spoken_name": "あおい"},
                {"name": "たくみ", "color": "blue", "spoken_name": "たくみ"},
            ]
        },
    )
    assert created.status_code == 201
    game = created.json()
    assert game["status"] == "ready"
    assert len(game["players"]) == 2

    started = request("POST", f"/api/games/{game['id']}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"

    rolled = request("POST", f"/api/games/{game['id']}/rolls", json={"value": 1})
    assert rolled.status_code == 200
    state = rolled.json()
    assert state["turnNumber"] == 1
    assert state["players"][0]["position"] == 38
    assert state["turns"][0]["normalPosition"] == 1
    assert state["turns"][0]["finalPosition"] == 38
    assert state["turns"][0]["jumpType"] == "ladder"


def test_rejects_game_with_fewer_than_two_players() -> None:
    response = request(
        "POST",
        "/api/games",
        json={"players": [{"name": "ひとり", "color": "red"}]},
    )
    assert response.status_code == 422

