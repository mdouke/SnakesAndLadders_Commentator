from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from commentator.domain.game import GameState, PlayerState
from commentator.main import MemoryGameRepository, create_app
from commentator.presets import load_board_preset
from commentator.services.commentary import (
    CommentaryRequest,
    CommentaryService,
    FakeCommentaryProvider,
)
from commentator.services.game import GameService
from commentator.services.recognition import RecognitionService
from fastapi.testclient import TestClient


class ManualScheduler:
    """Holds generation jobs so a test decides when each one finishes."""

    def __init__(self) -> None:
        self.jobs: list[Callable[[], None]] = []

    def __call__(self, job: Callable[[], None]) -> None:
        self.jobs.append(job)

    def run(self, index: int = 0) -> None:
        self.jobs.pop(index)()


class Table:
    def __init__(self, provider: Any = None) -> None:
        self.repository = MemoryGameRepository()
        self.games = GameService(self.repository)
        self.scheduler = ManualScheduler()
        self.commentary = CommentaryService(
            self.games,
            provider=provider or FakeCommentaryProvider(),
            scheduler=self.scheduler,
        )
        self.published: list[dict[str, Any]] = []
        self.commentary.subscribe(self.published.append)
        self.commentary.attach()
        self.game = self.new_game()

    def new_game(self) -> GameState:
        game = GameState.create(load_board_preset(), [
            PlayerState("a", "アオイ", "red"), PlayerState("b", "ハル", "blue"),
        ])
        game.start()
        with self.games.lock:
            self.games.save_or_503(game, make_current=True)
        return game

    def apply(self, action: Callable[[GameState], object]) -> GameState:
        with self.games.lock:
            game = self.repository.current()
            assert game is not None
            action(game)
            self.games.save_or_503(game)
            return game

    @property
    def state(self) -> dict[str, Any]:
        return self.commentary.snapshot()


def test_roll_generates_commentary_for_that_turn() -> None:
    table = Table()
    game = table.apply(lambda game: game.roll(3))
    turn = game.turns[-1]

    assert table.state["status"] == "generating"
    assert table.state["turnId"] == turn.id
    table.scheduler.run()

    assert table.state["status"] == "ready"
    assert table.state["turnId"] == turn.id
    assert table.state["provider"] == "fake"
    assert "アオイさん、出目は3" in table.state["text"]
    assert [item["status"] for item in table.published] == ["generating", "ready"]


def test_result_for_an_older_turn_is_discarded() -> None:
    table = Table()
    table.apply(lambda game: game.roll(3))
    game = table.apply(lambda game: game.roll(4))
    newest = game.turns[-1]

    table.scheduler.run(0)  # the first roll's job finishes late
    assert table.state["status"] == "generating"
    assert table.state["turnId"] == newest.id

    table.scheduler.run(0)
    assert table.state["status"] == "ready"
    assert table.state["turnId"] == newest.id
    assert "ハルさん、出目は4" in table.state["text"]


def test_undo_clears_pending_and_shown_commentary() -> None:
    table = Table()
    table.apply(lambda game: game.roll(3))
    table.apply(lambda game: game.undo())
    assert table.state["status"] == "idle"
    table.scheduler.run()
    assert table.state["status"] == "idle"  # the late result is not shown

    table.apply(lambda game: game.roll(5))
    table.scheduler.run()
    assert table.state["status"] == "ready"
    table.apply(lambda game: game.undo())
    assert table.state["status"] == "idle"
    assert table.state["text"] is None


def test_redo_comments_on_the_restored_turn() -> None:
    table = Table()
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    table.apply(lambda game: game.undo())
    game = table.apply(lambda game: game.redo())

    assert table.state["status"] == "generating"
    assert table.state["turnId"] == game.turns[-1].id


def test_correcting_the_newest_roll_comments_on_the_new_result() -> None:
    table = Table()
    game = table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    turn_id = game.turns[-1].id
    corrected = table.apply(
        lambda game: game.correct_roll(turn_id, 5, reason="誤入力", operator="admin")
    )

    assert table.state["status"] == "generating"
    table.scheduler.run()
    assert table.state["turnId"] == corrected.turns[-1].id
    assert "出目は5" in table.state["text"]


def test_pause_keeps_the_comment_and_switching_games_does_not_comment() -> None:
    table = Table()
    first = table.apply(lambda game: game.roll(6))
    table.scheduler.run()
    table.apply(lambda game: game.pause())
    assert table.state["status"] == "ready"
    assert table.state["turnId"] == first.turns[-1].id

    table.new_game()
    assert table.state["status"] == "idle"
    with table.games.lock:
        table.games.set_current(first.id)
    assert table.state["status"] == "idle"
    assert table.scheduler.jobs == []


def test_provider_failure_is_reported_without_touching_the_game() -> None:
    class BrokenProvider:
        name = "broken"

        def generate(self, request: CommentaryRequest) -> str:
            raise TimeoutError("provider timed out")

    table = Table(provider=BrokenProvider())
    game = table.apply(lambda game: game.roll(2))
    table.scheduler.run()

    assert table.state["status"] == "failed"
    assert table.state["text"] is None
    stored = table.repository.get(game.id)
    assert stored is not None and len(stored.turns) == 1


def request(**overrides: Any) -> CommentaryRequest:
    values: dict[str, Any] = {
        "game_id": "g",
        "turn_id": "t",
        "turn_number": 1,
        "player_name": "アオイ",
        "roll": 4,
        "before": 10,
        "normal_position": 14,
        "final_position": 14,
        "overshoot": False,
        "jump_type": None,
        "events": ("normal_move",),
        "goal": 100,
        "leader_name": None,
    }
    values.update(overrides)
    return CommentaryRequest(**values)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "10マス目から14マス目へ進みます。"),
        ({"jump_type": "ladder", "final_position": 38}, "ハシゴを登って一気に38マス目！"),
        ({"jump_type": "snake", "final_position": 6}, "ヘビに飲まれて6マス目まで落ちて"),
        ({"overshoot": True, "normal_position": 98}, "ゴールを超えてしまい、98マス目へ戻ります。"),
        ({"events": ("normal_move", "goal"), "final_position": 100}, "見事ゴール！"),
        ({"events": ("normal_move", "near_goal"), "final_position": 97}, "ゴールまであと3マス！"),
        ({"leader_name": "アオイ"}, "現在トップを走っています。"),
    ],
)
def test_fake_provider_describes_the_turn(overrides: dict[str, Any], expected: str) -> None:
    assert expected in FakeCommentaryProvider().generate(request(**overrides))


def test_websocket_streams_generating_then_ready() -> None:
    repository = MemoryGameRepository()
    games = GameService(repository)
    scheduler = ManualScheduler()
    commentary = CommentaryService(games, scheduler=scheduler, provider=FakeCommentaryProvider())
    recognition = RecognitionService(games, pipeline=None, camera_provider=None)
    app = create_app(
        repository=repository,
        recognition_service=recognition,
        commentary_service=commentary,
    )
    players = {"players": [{"name": "A", "color": "red"}, {"name": "B", "color": "blue"}]}
    with TestClient(app) as http, http.websocket_connect("/ws") as ws:
        for _ in range(3):
            ws.receive_json()
        game = http.post("/api/games", json=players).json()
        started = http.post(
            f"/api/games/{game['id']}/start", json={"expectedStateVersion": 0}
        ).json()
        http.post(
            f"/api/games/{game['id']}/rolls",
            json={"value": 2, "expectedStateVersion": started["stateVersion"]},
        )
        scheduler.run()
        messages = [ws.receive_json() for _ in range(5)]

    assert [message["type"] for message in messages] == [
        "game", "game", "game", "commentary", "commentary",
    ]
    assert messages[3]["payload"]["status"] == "generating"
    assert messages[4]["payload"]["status"] == "ready"
    assert messages[4]["payload"]["turnId"] == messages[2]["payload"]["turns"][-1]["id"]
