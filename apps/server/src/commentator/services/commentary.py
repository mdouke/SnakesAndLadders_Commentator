"""Turn commentary with a replaceable provider (a fake one until Phase 6).

The service reacts to game changes, generates commentary for the newest turn in a
background job, and publishes it only if that turn is still the newest turn of the
current game. Undo, a newer roll or switching games therefore never shows a stale
comment.

Lock order: game lock -> commentary lock -> realtime hub. Game listeners are called
with the game lock held; generation jobs run without any lock and take the game lock
before the commentary lock when they finish.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock, Thread
from time import monotonic, sleep
from typing import Any, Literal, Protocol

from .game import GameService

CommentaryStatus = Literal["idle", "generating", "ready", "failed"]
Scheduler = Callable[[Callable[[], None]], None]
CommentarySubscriber = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class CommentaryRequest:
    """Structured context for one turn; the future LLM prompt is built from this."""

    game_id: str
    turn_id: str
    turn_number: int
    player_name: str
    roll: int
    before: int
    normal_position: int
    final_position: int
    overshoot: bool
    jump_type: str | None
    events: tuple[str, ...]
    goal: int
    leader_name: str | None


class CommentaryProvider(Protocol):
    name: str

    def generate(self, request: CommentaryRequest) -> str: ...


class FakeCommentaryProvider:
    """Deterministic template commentary with an optional artificial delay."""

    name = "fake"

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        if delay_seconds < 0:
            raise ValueError("生成の待ち時間は0以上にしてください")
        self.delay_seconds = delay_seconds

    def generate(self, request: CommentaryRequest) -> str:
        if self.delay_seconds:
            sleep(self.delay_seconds)
        name = request.player_name
        parts = [f"{name}さん、出目は{request.roll}！"]
        if request.overshoot:
            parts.append(
                f"ゴールを超えてしまい、{request.normal_position}マス目へ戻ります。"
            )
        else:
            parts.append(
                f"{request.before}マス目から{request.normal_position}マス目へ進みます。"
            )
        if request.jump_type == "ladder":
            parts.append(f"ハシゴを登って一気に{request.final_position}マス目！")
        elif request.jump_type == "snake":
            parts.append(f"ヘビに飲まれて{request.final_position}マス目まで落ちてしまいました。")
        if "goal" in request.events:
            parts.append("見事ゴール！おめでとうございます！")
        elif "near_goal" in request.events:
            parts.append(f"ゴールまであと{request.goal - request.final_position}マス！")
        elif request.leader_name == name and request.final_position > 0:
            parts.append("現在トップを走っています。")
        return "".join(parts)


def run_in_thread(job: Callable[[], None]) -> None:
    Thread(target=job, name="commentary", daemon=True).start()


class CommentaryService:
    def __init__(
        self,
        game_service: GameService,
        *,
        provider: CommentaryProvider | None = None,
        scheduler: Scheduler = run_in_thread,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.game_service = game_service
        self.provider = provider or FakeCommentaryProvider(delay_seconds=0.8)
        self.scheduler = scheduler
        self.clock = clock
        self._lock = RLock()
        self._target: tuple[str, str] | None = None
        self._known: _TurnMarker | None = None
        self._state: dict[str, Any] = _idle()
        self._subscribers: list[CommentarySubscriber] = []

    def attach(self) -> None:
        """Start reacting to game changes."""
        self.game_service.add_listener(self.game_changed)

    def subscribe(self, callback: CommentarySubscriber) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def send_snapshot(self, callback: CommentarySubscriber) -> None:
        with self._lock:
            callback(dict(self._state))

    def game_changed(self, game: dict[str, Any] | None) -> None:
        """Game listener; runs under the game lock and must not block."""
        with self._lock:
            previous, current = self._known, _turn_marker(game)
            self._known = current
            if current == previous:
                return  # e.g. pause, resume, position correction: keep the comment
            if game is None or current is None or not _should_comment(previous, current):
                self._target = None
                if self._state["status"] != "idle":
                    self._set_state(_idle())
                return
            assert current.turn_id is not None
            self._target = (current.game_id, current.turn_id)
            request = _build_request(game, current.turn_id)
            self._set_state({
                **_idle(),
                "status": "generating",
                "gameId": request.game_id,
                "turnId": request.turn_id,
                "turnNumber": request.turn_number,
            })
        self.scheduler(lambda: self._generate(request))

    def _generate(self, request: CommentaryRequest) -> None:
        started = self.clock()
        text: str | None = None
        error: Exception | None = None
        try:
            text = self.provider.generate(request)
            if not text.strip():
                raise ValueError("空のコメントが生成されました")
        except Exception as caught:  # provider failures must not reach the game
            error = caught
        duration_ms = round((self.clock() - started) * 1000, 3)
        with self.game_service.lock, self._lock:
            if self._target != (request.game_id, request.turn_id):
                logging.getLogger("commentary").info(
                    "stale commentary discarded",
                    extra={
                        "event": "commentary_discarded",
                        "gameId": request.game_id,
                        "turnId": request.turn_id,
                    },
                )
                return
            base = {
                "gameId": request.game_id,
                "turnId": request.turn_id,
                "turnNumber": request.turn_number,
                "provider": self.provider.name,
                "durationMs": duration_ms,
            }
            if error is not None or text is None:
                logging.getLogger("commentary").error(
                    "commentary generation failed",
                    exc_info=error,
                    extra={
                        "event": "commentary_failed",
                        "gameId": request.game_id,
                        "turnId": request.turn_id,
                        "provider": self.provider.name,
                    },
                )
                self._set_state({**_idle(), **base, "status": "failed"})
                return
            logging.getLogger("commentary").info(
                "commentary generated",
                extra={
                    "event": "commentary_generated",
                    "gameId": request.game_id,
                    "turnId": request.turn_id,
                    "provider": self.provider.name,
                    "durationMs": duration_ms,
                },
            )
            self._set_state({
                **_idle(),
                **base,
                "status": "ready",
                "text": text,
                "generatedAt": datetime.now(UTC).isoformat(),
            })

    def _set_state(self, state: dict[str, Any]) -> None:
        self._state = state
        for subscriber in tuple(self._subscribers):
            try:
                subscriber(dict(state))
            except Exception:
                logging.getLogger("commentary").exception(
                    "commentary subscriber failed",
                    extra={"event": "commentary_subscriber_failed"},
                )


def _idle() -> dict[str, Any]:
    return {
        "status": "idle",
        "gameId": None,
        "turnId": None,
        "turnNumber": None,
        "text": None,
        "provider": None,
        "durationMs": None,
        "generatedAt": None,
    }


@dataclass(frozen=True, slots=True)
class _TurnMarker:
    game_id: str
    turn_id: str | None
    turn_count: int


def _turn_marker(game: dict[str, Any] | None) -> _TurnMarker | None:
    if game is None:
        return None
    turns = game["turns"]
    return _TurnMarker(game["id"], turns[-1]["id"] if turns else None, len(turns))


def _should_comment(previous: _TurnMarker | None, current: _TurnMarker) -> bool:
    """Comment only when the same game gained or replaced its newest turn.

    - A roll or redo adds a turn: comment on it.
    - Correcting the newest roll replaces that turn: comment on the new result.
    - Undo removes the newest turn: clear, and do not re-comment the older turn.
    - A different game (new, activated, or the first state after startup): clear,
      because its newest turn is not something that just happened.
    """
    if current.turn_id is None or previous is None:
        return False
    if previous.game_id != current.game_id:
        return False
    if current.turn_count > previous.turn_count:
        return True
    return current.turn_count == previous.turn_count and current.turn_id != previous.turn_id


def _build_request(game: dict[str, Any], turn_id: str) -> CommentaryRequest:
    turn = next(item for item in game["turns"] if item["id"] == turn_id)
    players = {player["id"]: player for player in game["players"]}
    player = players[turn["playerId"]]
    positions = [item["position"] for item in game["players"]]
    top = max(positions, default=0)
    leaders = [item for item in game["players"] if item["position"] == top]
    return CommentaryRequest(
        game_id=game["id"],
        turn_id=turn["id"],
        turn_number=turn["number"],
        player_name=player["displayName"],
        roll=turn["roll"],
        before=turn["before"],
        normal_position=turn["normalPosition"],
        final_position=turn["finalPosition"],
        overshoot=turn["overshoot"],
        jump_type=turn["jumpType"],
        events=tuple(turn["events"]),
        goal=player["position"] + player["remainingSquares"],
        # A tie has no single leader.
        leader_name=leaders[0]["displayName"] if len(leaders) == 1 else None,
    )
