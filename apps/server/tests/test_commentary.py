from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from commentator.domain.game import GameState, PlayerState
from commentator.infrastructure.persistence.commentary_log import (
    MemoryCommentaryLog,
    SQLiteCommentaryLog,
)
from commentator.main import MemoryGameRepository, create_app
from commentator.presets import load_board_preset
from commentator.services.commentary import CommentaryError, CommentaryService
from commentator.services.game import GameService
from commentator.services.llm.context import (
    CommentaryContext,
    CommentaryPrompt,
    build_context,
    build_prompt,
)
from commentator.services.llm.providers import (
    FakeCommentaryProvider,
    ProviderError,
    ProviderOutput,
    template_commentary,
)
from commentator.services.llm.settings import CommentarySettings, CommentarySettingsStore
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


class ScriptedProvider:
    """Returns or raises the given results in order."""

    name = "scripted"

    def __init__(self, *results: str | Exception | ProviderOutput) -> None:
        self.results = list(results)
        self.prompts: list[CommentaryPrompt] = []

    def generate(self, prompt: CommentaryPrompt, *, timeout: float) -> ProviderOutput:
        self.prompts.append(prompt)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ProviderOutput):
            return result
        return ProviderOutput(text=result, model="scripted-1", request_id="req_1")


class Table:
    def __init__(self, provider: Any = None, **settings: Any) -> None:
        self.repository = MemoryGameRepository()
        self.games = GameService(self.repository)
        self.scheduler = ManualScheduler()
        self.log = MemoryCommentaryLog()
        self.settings = CommentarySettingsStore()
        if settings:
            self.settings.update(settings)
        self.sleeps: list[float] = []
        self.commentary = CommentaryService(
            self.games,
            providers={"fake": provider or FakeCommentaryProvider()},
            settings=self.settings,
            log=self.log,
            scheduler=self.scheduler,
            sleep=self.sleeps.append,
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

    def row(self, request_id: str | None = None) -> dict[str, Any]:
        row = self.log.get(request_id or self.state["requestId"])
        assert row is not None
        return row


# -- reacting to the game ------------------------------------------------------


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
    assert table.state["source"] == "generated"
    assert "アオイさん、出目は3" in table.state["text"]
    assert [item["status"] for item in table.published] == ["generating", "ready"]


def test_result_for_an_older_turn_is_discarded_and_stored() -> None:
    table = Table()
    table.apply(lambda game: game.roll(3))
    first_request = table.state["requestId"]
    game = table.apply(lambda game: game.roll(4))
    newest = game.turns[-1]

    table.scheduler.run(0)  # the first roll's job finishes late
    assert table.state["status"] == "generating"
    assert table.state["turnId"] == newest.id
    assert table.row(first_request)["status"] == "discarded"

    table.scheduler.run(0)
    assert table.state["status"] == "ready"
    assert "ハルさん、出目は4" in table.state["text"]
    assert table.row()["status"] == "displayed"


def test_results_arriving_out_of_order_never_show_the_older_one() -> None:
    table = Table()
    table.apply(lambda game: game.roll(3))
    table.apply(lambda game: game.roll(4))
    table.scheduler.run(1)  # the newest finishes first
    shown = table.state["text"]
    table.scheduler.run(0)  # then the older one
    assert table.state["text"] == shown
    assert "ハルさん" in shown


def test_undo_clears_pending_and_shown_commentary() -> None:
    table = Table()
    table.apply(lambda game: game.roll(3))
    table.apply(lambda game: game.undo())
    assert table.state["status"] == "idle"
    table.scheduler.run()
    assert table.state["status"] == "idle"  # the late result is not shown

    table.apply(lambda game: game.resume())  # Undo paused the game
    table.apply(lambda game: game.roll(5))
    table.scheduler.run()
    shown = table.state["requestId"]
    table.apply(lambda game: game.undo())
    assert table.state["status"] == "idle"
    assert table.state["text"] is None
    assert table.row(shown)["status"] == "superseded"


def test_redo_comments_on_the_restored_turn() -> None:
    table = Table()
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    table.apply(lambda game: game.undo())
    game = table.apply(lambda game: game.redo())

    assert table.state["status"] == "generating"
    assert table.state["turnId"] == game.turns[-1].id


def test_correcting_the_newest_roll_replaces_the_comment() -> None:
    table = Table()
    game = table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    old_request = table.state["requestId"]
    turn_id = game.turns[-1].id
    corrected = table.apply(
        lambda game: game.correct_roll(turn_id, 5, reason="誤入力", operator="admin")
    )

    assert table.state["status"] == "generating"
    assert table.row(old_request)["status"] == "superseded"
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


@pytest.mark.parametrize("end", ["abort", "suspend"])
def test_ending_the_game_clears_the_comment_and_reopening_does_not_repeat_it(end: str) -> None:
    table = Table()
    table.apply(lambda game: game.roll(6))
    table.scheduler.run()
    table.apply(lambda game: getattr(game, end)())
    assert table.state["status"] == "idle"

    table.apply(lambda game: game.reopen() if end == "abort" else game.resume())
    assert table.state["status"] == "idle"
    assert table.scheduler.jobs == []


# -- provider failures, retries and the fallback --------------------------------


def test_provider_failure_falls_back_to_the_template_without_touching_the_game() -> None:
    provider = ScriptedProvider(
        ProviderError("auth", code="provider_auth", retryable=False)
    )
    table = Table(provider=provider)
    game = table.apply(lambda game: game.roll(2))
    table.scheduler.run()

    assert table.state["status"] == "ready"
    assert table.state["source"] == "fallback"
    assert table.state["fallbackReason"] == "provider_auth"
    assert "アオイさん、出目は2" in table.state["text"]
    assert len(provider.prompts) == 1  # not retried
    row = table.row()
    assert row["outcome"] == "fallback"
    assert row["errors"] == [{"code": "provider_auth", "message": "auth"}]
    stored = table.repository.get(game.id)
    assert stored is not None and len(stored.turns) == 1


def test_retryable_errors_are_retried_with_backoff() -> None:
    provider = ScriptedProvider(
        ProviderError("slow", code="provider_timeout", retryable=True),
        "アオイさん、3が出ました！",
    )
    table = Table(provider=provider, maxRetries=2)
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()

    assert table.state["source"] == "generated"
    assert table.state["text"] == "アオイさん、3が出ました！"
    assert table.row()["attempts"] == 2
    assert table.sleeps == [0.5]


def test_retries_stop_after_the_limit() -> None:
    errors = [ProviderError("down", code="provider_status", retryable=True) for _ in range(3)]
    provider = ScriptedProvider(*errors)
    table = Table(provider=provider, maxRetries=1)
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()

    assert len(provider.prompts) == 2
    assert table.state["source"] == "fallback"
    assert table.row()["attempts"] == 2


def test_retries_stop_when_a_newer_turn_takes_over() -> None:
    table = Table(maxRetries=3)

    class SlowFailure:
        name = "slow"
        calls = 0

        def generate(self, prompt: CommentaryPrompt, *, timeout: float) -> ProviderOutput:
            SlowFailure.calls += 1
            if SlowFailure.calls == 1:
                table.apply(lambda game: game.roll(4))  # a newer roll meanwhile
            raise ProviderError("down", code="provider_status", retryable=True)

    table.commentary.providers["fake"] = SlowFailure()
    table.apply(lambda game: game.roll(3))
    table.scheduler.run(0)
    assert SlowFailure.calls == 1


def test_unexpected_provider_exceptions_are_contained() -> None:
    table = Table(provider=ScriptedProvider(KeyError("bug")))
    table.apply(lambda game: game.roll(2))
    table.scheduler.run()
    assert table.state["source"] == "fallback"
    assert table.state["fallbackReason"] == "provider_error"


# -- checking the text ----------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ("   ", "empty"),
        ("ハルさんはバカだね", "blocked_word"),
        ("あ" * 400, "too_long"),
    ],
)
def test_unusable_responses_are_replaced(response: str, reason: str) -> None:
    table = Table(provider=ScriptedProvider(response))
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    assert table.state["source"] == "fallback"
    assert table.state["fallbackReason"] == reason
    assert table.row()["response_text"] == response


def test_long_responses_are_cut_at_a_sentence_end() -> None:
    long_text = "アオイさん、出目は3！" + "盛り上がってきました。" * 20
    table = Table(provider=ScriptedProvider(long_text), length="short")
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    text = table.state["text"]
    assert table.state["source"] == "generated"
    assert len(text) <= 70 and text.endswith("。")


def test_markdown_and_quotes_are_removed_for_reading_aloud() -> None:
    table = Table(provider=ScriptedProvider("「**アオイさん**、\n出目は3！」"))
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    assert table.state["text"] == "アオイさん、 出目は3！"


def test_a_truncated_response_is_not_shown_as_complete() -> None:
    output = ProviderOutput(text="アオイさん、出目は", model="m", truncated=True)
    table = Table(provider=ScriptedProvider(output))
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    assert table.state["source"] == "fallback"
    assert table.state["fallbackReason"] == "truncated"


# -- regeneration, on/off and storage --------------------------------------------


def test_regenerate_replaces_the_shown_comment_and_keeps_the_old_one() -> None:
    table = Table(provider=ScriptedProvider("一つ目の実況です。", "二つ目の実況です。"))
    game = table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    first = table.state["requestId"]

    state = table.commentary.regenerate(game.turns[-1].id)
    assert state["status"] == "generating"
    assert state["revision"] == 1
    assert table.row(first)["status"] == "superseded"
    table.scheduler.run()
    assert table.state["text"] == "二つ目の実況です。"
    assert table.row()["trigger"] == "regenerate"
    assert [row["status"] for row in table.log.list()] == ["displayed", "superseded"]


def test_regenerate_rejects_old_turns_and_disabled_commentary() -> None:
    table = Table()
    game = table.apply(lambda game: game.roll(3))
    first = game.turns[-1].id
    table.apply(lambda game: game.roll(4))
    with pytest.raises(CommentaryError) as caught:
        table.commentary.regenerate(first)
    assert caught.value.code == "not_newest_turn"

    table.settings.update({"enabled": False})
    with pytest.raises(CommentaryError) as caught:
        table.commentary.regenerate()
    assert caught.value.code == "commentary_disabled"


def test_switching_commentary_off_clears_and_stops_generating() -> None:
    table = Table()
    table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    table.settings.update({"enabled": False})
    table.commentary.settings_changed()
    assert table.state["status"] == "idle"

    table.apply(lambda game: game.roll(4))
    assert table.state["status"] == "idle"
    assert table.scheduler.jobs == []


def test_the_request_log_keeps_prompt_response_and_timing() -> None:
    table = Table(provider=ScriptedProvider("アオイさん、いい出目です。"))
    game = table.apply(lambda game: game.roll(3))
    table.scheduler.run()
    row = table.row()
    assert row["game_id"] == game.id
    assert row["turn_id"] == game.turns[-1].id
    assert row["prompt_version"] == "commentary-v1"
    assert '"roll": 3' in row["user_prompt"]
    assert "ヘビとハシゴ" in row["system_prompt"]
    assert row["response_text"] == "アオイさん、いい出目です。"
    assert row["final_text"] == "アオイさん、いい出目です。"
    assert row["model"] == "scripted-1"
    assert row["provider_request_ids"] == ["req_1"]
    assert row["duration_ms"] is not None and row["finished_at"] is not None


def test_sqlite_log_round_trip(tmp_path: Path) -> None:
    log = SQLiteCommentaryLog(tmp_path / "games.sqlite3")
    log.start({
        "id": "r1", "game_id": "g", "turn_id": "t", "state_version": 3, "revision": 0,
        "trigger": "turn", "provider": "claude", "model": "claude-opus-5-5",
        "prompt_version": "commentary-v1", "system_prompt": "s", "user_prompt": "u",
        "status": "pending", "started_at": "2026-11-18T00:00:00+00:00",
    })
    log.update("r1", status="displayed", errors=[{"code": "x", "message": "y"}],
               final_text="こんにちは", attempts=2)
    reopened = SQLiteCommentaryLog(tmp_path / "games.sqlite3")
    row = reopened.get("r1")
    assert row is not None
    assert row["status"] == "displayed"
    assert row["errors"] == [{"code": "x", "message": "y"}]
    assert row["attempts"] == 2
    assert [item["id"] for item in reopened.list(game_id="g")] == ["r1"]
    with pytest.raises(ValueError):
        reopened.update("r1", api_key="secret")


# -- context, prompt and template -------------------------------------------------


def game_payload() -> dict[str, Any]:
    game = GameState.create(load_board_preset(), [
        PlayerState("a", "アオイ", "red"), PlayerState("b", "ハル", "blue"),
    ])
    game.start()
    game.roll(3)
    game.roll(5)
    return game.to_dict()


def test_context_contains_standings_and_recent_turns() -> None:
    payload = game_payload()
    context = build_context(payload, payload["turns"][-1]["id"], CommentarySettings())
    assert context.player_name == "ハル"
    assert [player.name for player in context.players] == ["アオイ", "ハル"]
    assert [turn.player for turn in context.recent_turns] == ["アオイ"]
    assert context.goal == 100


def test_names_can_be_left_out() -> None:
    payload = game_payload()
    settings = CommentarySettings(use_names=False)
    context = build_context(payload, payload["turns"][-1]["id"], settings)
    prompt = build_prompt(context, settings)
    assert context.player_name == "プレイヤー2"
    assert "アオイ" not in prompt.user and "ハル" not in prompt.user
    assert template_commentary(context, language="ja", honorific=False).startswith(
        "プレイヤー2、出目は5"
    )


def test_prompt_follows_language_length_and_style() -> None:
    payload = game_payload()
    settings = CommentarySettings(language="en", length="short", style="calm")
    prompt = build_prompt(build_context(payload, payload["turns"][-1]["id"], settings), settings)
    assert "About 20 words" in prompt.system
    assert "Calm" in prompt.system
    assert prompt.max_length == 35


def context(**overrides: Any) -> CommentaryContext:
    values: dict[str, Any] = {
        "game_id": "g", "turn_id": "t", "state_version": 1, "turn_number": 1,
        "player_name": "アオイ", "roll": 4, "before": 10, "normal_position": 14,
        "final_position": 14, "overshoot": False, "jump_type": None,
        "events": ("normal_move",), "goal": 100, "leader_name": None,
    }
    values.update(overrides)
    return CommentaryContext(**values)


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
def test_template_describes_the_turn(overrides: dict[str, Any], expected: str) -> None:
    assert expected in template_commentary(context(**overrides), language="ja", honorific=True)


def test_fake_provider_uses_the_template() -> None:
    settings = CommentarySettings()
    prompt = build_prompt(context(), settings)
    output = FakeCommentaryProvider().generate(prompt, timeout=1)
    assert output.text.startswith("アオイさん、出目は4！")
    assert replace(settings, language="en").language == "en"


# -- HTTP API and WebSocket -----------------------------------------------------


def api_table() -> tuple[TestClient, ManualScheduler]:
    repository = MemoryGameRepository()
    games = GameService(repository)
    scheduler = ManualScheduler()
    commentary = CommentaryService(
        games, scheduler=scheduler, providers={"fake": FakeCommentaryProvider()}
    )
    recognition = RecognitionService(games, pipeline=None, camera_provider=None)
    app = create_app(
        repository=repository, recognition_service=recognition, commentary_service=commentary,
    )
    return TestClient(app), scheduler


PLAYERS = {"players": [{"name": "A", "color": "red"}, {"name": "B", "color": "blue"}]}


def start_game(http: TestClient) -> dict[str, Any]:
    game = http.post("/api/games", json=PLAYERS).json()
    started: dict[str, Any] = http.post(
        f"/api/games/{game['id']}/start", json={"expectedStateVersion": 0}
    ).json()
    return started


def test_websocket_streams_generating_then_ready() -> None:
    http, scheduler = api_table()
    with http, http.websocket_connect("/ws") as ws:
        for _ in range(4):
            ws.receive_json()
        started = start_game(http)
        http.post(
            f"/api/games/{started['id']}/rolls",
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


def test_settings_api_validates_and_never_accepts_keys() -> None:
    http, _ = api_table()
    with http:
        current = http.get("/api/settings/commentary").json()
        assert current["provider"] == "fake"
        assert set(current["providers"]) == {"fake", "claude", "openai"}

        changed = http.put(
            "/api/settings/commentary",
            json={"provider": "claude", "length": "short", "useNames": False},
        )
        assert changed.status_code == 200
        assert changed.json()["provider"] == "claude"
        assert changed.json()["useNames"] is False

        assert http.put("/api/settings/commentary", json={"apiKey": "sk"}).status_code == 422
        assert http.put("/api/settings/commentary", json={"timeoutSeconds": 0}).status_code == 422
        assert http.put("/api/settings/commentary", json={"provider": None}).status_code == 422


def test_regenerate_and_request_log_api() -> None:
    http, scheduler = api_table()
    with http:
        assert http.post("/api/commentary/regenerate", json={}).status_code == 409
        started = start_game(http)
        http.post(
            f"/api/games/{started['id']}/rolls",
            json={"value": 2, "expectedStateVersion": started["stateVersion"]},
        )
        scheduler.run()
        regenerated = http.post("/api/commentary/regenerate", json={})
        assert regenerated.status_code == 200
        assert regenerated.json()["revision"] == 1
        scheduler.run()

        assert http.get("/api/commentary").json()["status"] == "ready"
        log = http.get("/api/commentary/requests", params={"gameId": started["id"]}).json()
        assert [item["status"] for item in log["requests"]] == ["displayed", "superseded"]
        assert log["requests"][0]["trigger"] == "regenerate"
