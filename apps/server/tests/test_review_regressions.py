from datetime import UTC, datetime

import pytest
from commentator.domain.game import EventType, GameState, PlayerState, RollInput, replay
from commentator.main import MemoryGameRepository, create_app
from commentator.presets import load_board_preset
from httpx import ASGITransport, AsyncClient

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def setup_game(position: int = 0) -> GameState:
    return GameState.create(load_board_preset(), [
        PlayerState("a", "A", "red", position=position), PlayerState("b", "B", "blue")
    ], game_id="game")


def test_branch_ids_and_deterministic_replay() -> None:
    initial = setup_game()
    original = replay(initial, [RollInput(1, NOW)], started_at=NOW)
    corrected = replay(initial, [RollInput(2, NOW)], started_at=NOW, branch_id="correction-1")
    again = replay(initial, [RollInput(2, NOW)], started_at=NOW, branch_id="correction-1")
    assert original.turns[0].id != corrected.turns[0].id
    assert corrected.to_dict() == again.to_dict()
    assert initial.branch_id == "main"


def test_snake_head_counts_as_maximum_position() -> None:
    game = setup_game(92)
    game.start(now=NOW)
    game.roll(1, now=NOW)
    assert game.players[0].position == 73
    assert game.players[0].statistics.maximum_position == 93


def test_resume_keeps_all_suspension_times() -> None:
    game = setup_game()
    game.start(now=NOW)
    for _ in range(2):
        game.suspend(now=NOW)
        game.resume()
    assert game.suspended_at == NOW
    assert game.suspension_history == [NOW, NOW]
    assert game.to_dict()["suspensionHistory"] == [NOW.isoformat(), NOW.isoformat()]


@pytest.mark.parametrize(("position", "roll", "events"), [
    (99, 6, {EventType.GOAL_OVERSHOOT, EventType.LARGE_ROLL, EventType.NEAR_GOAL}),
    (99, 2, {EventType.GOAL_OVERSHOOT, EventType.SMALL_ROLL, EventType.NEAR_GOAL}),
    (0, 3, {EventType.NORMAL_MOVE}),
    (0, 1, {EventType.NORMAL_MOVE, EventType.SMALL_ROLL, EventType.LADDER}),
    (92, 1, {EventType.NORMAL_MOVE, EventType.SMALL_ROLL, EventType.SNAKE}),
    (99, 1, {EventType.NORMAL_MOVE, EventType.SMALL_ROLL, EventType.GOAL}),
])
def test_events(position: int, roll: int, events: set[EventType]) -> None:
    game = setup_game(position)
    game.start(now=NOW)
    assert set(game.roll(roll, now=NOW).events) == events


def test_rank_ties_after_someone_finishes() -> None:
    game = setup_game(99)
    game.players.extend([PlayerState("c", "C", "green")])
    game.start(now=NOW)
    game.roll(1, now=NOW)
    assert game.rankings() == {"a": 1, "b": 2, "c": 2}


def test_factory_isolation_validation_and_invalid_transitions() -> None:
    import asyncio

    async def scenario() -> None:
        repo = MemoryGameRepository()
        async with AsyncClient(
            transport=ASGITransport(app=create_app(repository=repo)), base_url="http://test"
        ) as client:
            payload = {"players": [{"name": " A ", "color": "red", "spokenName": "エー"},
                                   {"name": "B", "color": "blue"}]}
            created = (await client.post("/api/games", json=payload)).json()
            assert created["players"][0]["name"] == "A"
            assert created["players"][0]["spokenName"] == "エー"
            path = f"/api/games/{created['id']}"
            assert (await client.post(path + "/resume")).status_code == 409
            assert (await client.post(path + "/abort")).status_code == 200
            assert (await client.post(path + "/start")).status_code == 409
            created = (await client.post("/api/games", json=payload)).json()
            path = f"/api/games/{created['id']}"
            await client.post(path + "/start")
            await client.post(path + "/pause")
            response = await client.post(path + "/rolls",
                                         json={"value": 1, "expectedTurnNumber": 0})
            assert response.status_code == 409
            assert repo.current() is not None
            payload["players"][0]["name"] = "   "
            assert (await client.post("/api/games", json=payload)).status_code == 422
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://test"
        ) as isolated:
            assert (await isolated.get("/api/games/current")).status_code == 404
    asyncio.run(scenario())
