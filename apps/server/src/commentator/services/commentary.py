"""Turn commentary: picks a provider, retries, checks the text and stores every request.

The service reacts to game changes, generates commentary for the newest turn in a
background job, and publishes it only if that request is still the current one.
Undo, a newer roll, a regeneration or switching games therefore never shows (or
speaks) a stale comment; the late result is stored as ``discarded``.

Lock order: game lock -> commentary lock -> realtime hub. Game listeners are called
with the game lock held; generation jobs run without any lock and take the game lock
before the commentary lock when they finish.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock, Thread
from time import monotonic
from time import sleep as default_sleep
from typing import Any, Literal
from uuid import uuid4

from ..infrastructure.persistence.commentary_log import (
    CommentaryLog,
    MemoryCommentaryLog,
)
from .game import GameService
from .llm.context import (
    PROMPT_VERSION,
    CommentaryContext,
    CommentaryPrompt,
    build_context,
    build_prompt,
    build_system_prompt,
)
from .llm.providers import (
    ClaudeCommentaryProvider,
    CommentaryProvider,
    FakeCommentaryProvider,
    OpenAICommentaryProvider,
    ProviderError,
    ProviderOutput,
    template_commentary,
)
from .llm.safety import review
from .llm.settings import CommentarySettings, CommentarySettingsStore

CommentaryStatus = Literal["idle", "generating", "ready", "failed"]
Scheduler = Callable[[Callable[[], None]], None]
CommentarySubscriber = Callable[[dict[str, Any]], None]

# Games in these states stop talking: nothing new is generated and the comment
# (and any speech) is cleared.
_SILENT_STATUSES = {"aborted", "suspended"}

__all__ = [
    "CommentaryContext",
    "CommentaryError",
    "CommentaryService",
    "FakeCommentaryProvider",
]


class CommentaryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def run_in_thread(job: Callable[[], None]) -> None:
    Thread(target=job, name="commentary", daemon=True).start()


def default_providers() -> dict[str, CommentaryProvider]:
    return {
        "fake": FakeCommentaryProvider(delay_seconds=0.8),
        "claude": ClaudeCommentaryProvider(),
        "openai": OpenAICommentaryProvider(),
    }


@dataclass(frozen=True, slots=True)
class _Job:
    request_id: str
    game_id: str
    turn_id: str
    turn_number: int
    revision: int
    prompt: CommentaryPrompt
    provider: CommentaryProvider


class CommentaryService:
    def __init__(
        self,
        game_service: GameService,
        *,
        providers: Mapping[str, CommentaryProvider] | None = None,
        settings: CommentarySettingsStore | None = None,
        log: CommentaryLog | None = None,
        scheduler: Scheduler = run_in_thread,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = default_sleep,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        self.game_service = game_service
        self.providers = dict(providers) if providers is not None else default_providers()
        self.settings = settings or CommentarySettingsStore()
        self.log: CommentaryLog = log if log is not None else MemoryCommentaryLog()
        self.scheduler = scheduler
        self.clock = clock
        self.sleep = sleep
        self.retry_delay_seconds = retry_delay_seconds
        self._lock = RLock()
        self._target: str | None = None  # request ID allowed to reach the screen
        self._displayed: str | None = None  # request ID currently on screen
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

    def is_current(self, request_id: str | None) -> bool:
        """True while ``request_id`` is the comment that may be shown and spoken."""
        with self._lock:
            return request_id is not None and request_id == self._target

    # -- reacting to the game -------------------------------------------------

    def game_changed(self, game: dict[str, Any] | None) -> None:
        """Game listener; runs under the game lock and must not block."""
        job: _Job | None = None
        with self._lock:
            previous, current = self._known, _turn_marker(game)
            self._known = current
            if game is not None and game["status"] in _SILENT_STATUSES:
                self._clear()
                return
            if current == previous:
                return  # e.g. pause, resume, position correction: keep the comment
            if game is None or current is None or not _should_comment(previous, current):
                self._clear()
                return
            assert current.turn_id is not None
            if not self.settings.get().enabled:
                self._clear()
                return
            job = self._start(game, current.turn_id, trigger="turn", revision=0)
        self.scheduler(lambda: self._generate(job))

    def regenerate(self, turn_id: str | None = None) -> dict[str, Any]:
        """Generate the newest turn's comment again; the shown one becomes superseded."""
        with self.game_service.lock:
            game = self.game_service.current_payload()
            with self._lock:
                if game is None or not game["turns"]:
                    raise CommentaryError("no_turn", "実況できるターンがありません")
                if game["status"] in _SILENT_STATUSES:
                    raise CommentaryError(
                        "game_not_active", "終了・中止したゲームでは再生成できません"
                    )
                newest = game["turns"][-1]["id"]
                if turn_id is not None and turn_id != newest:
                    raise CommentaryError(
                        "not_newest_turn", "再生成できるのは最新ターンの実況だけです"
                    )
                if not self.settings.get().enabled:
                    raise CommentaryError("commentary_disabled", "実況がオフになっています")
                revision = (
                    int(self._state["revision"]) + 1
                    if self._state["turnId"] == newest and self._state["revision"] is not None
                    else 0
                )
                job = self._start(game, newest, trigger="regenerate", revision=revision)
                state = dict(self._state)
        self.scheduler(lambda: self._generate(job))
        return state

    def prompt_preview(self) -> dict[str, Any]:
        """The prompt the next comment would be made with (for the admin screen).

        The system prompt follows the current settings. The user prompt is built
        for the newest turn of the current game; without a turn there is none.
        """
        settings = self.settings.get()
        with self.game_service.lock:
            game = self.game_service.current_payload()
        user: str | None = None
        turn_number: int | None = None
        if game is not None and game["turns"]:
            context = build_context(game, game["turns"][-1]["id"], settings)
            user = build_prompt(context, settings).user
            turn_number = context.turn_number
        return {
            "promptVersion": PROMPT_VERSION,
            "provider": settings.provider,
            "model": settings.model,
            "system": build_system_prompt(settings),
            "user": user,
            "turnNumber": turn_number,
        }

    def settings_changed(self) -> None:
        """Turning commentary off clears the screen and cancels pending speech."""
        with self.game_service.lock, self._lock:
            if not self.settings.get().enabled:
                self._clear()

    # -- internals ------------------------------------------------------------

    def _start(self, game: dict[str, Any], turn_id: str, *, trigger: str, revision: int) -> _Job:
        settings = self.settings.get()
        context = build_context(game, turn_id, settings)
        prompt = build_prompt(context, settings)
        provider = self.providers.get(settings.provider) or self.providers["fake"]
        request_id = uuid4().hex
        self.log.start({
            "id": request_id,
            "game_id": context.game_id,
            "turn_id": turn_id,
            "state_version": context.state_version,
            "revision": revision,
            "trigger": trigger,
            "provider": provider.name,
            "model": settings.model,
            "prompt_version": prompt.version,
            "system_prompt": prompt.system,
            "user_prompt": prompt.user,
            "status": "pending",
            "attempts": 0,
            "errors": [],
            "provider_request_ids": [],
            "started_at": _now(),
        })
        # A displayed comment that is being replaced keeps its row, marked superseded.
        self._supersede_displayed()
        self._target = request_id
        self._set_state({
            **_idle(),
            "status": "generating",
            "gameId": context.game_id,
            "turnId": turn_id,
            "turnNumber": context.turn_number,
            "requestId": request_id,
            "revision": revision,
            "provider": provider.name,
        })
        return _Job(
            request_id=request_id,
            game_id=context.game_id,
            turn_id=turn_id,
            turn_number=context.turn_number,
            revision=revision,
            prompt=prompt,
            provider=provider,
        )

    def _clear(self) -> None:
        self._target = None
        self._supersede_displayed()
        if self._state["status"] != "idle":
            self._set_state(_idle())

    def _supersede_displayed(self) -> None:
        if self._displayed is not None:
            self.log.update(self._displayed, status="superseded")
            self._displayed = None

    def _call_provider(
        self, job: _Job, settings: CommentarySettings
    ) -> tuple[ProviderOutput | None, list[dict[str, Any]], int]:
        errors: list[dict[str, Any]] = []
        attempts = 0
        for attempt in range(settings.max_retries + 1):
            if attempt and not self.is_current(job.request_id):
                break  # a newer request took over; stop spending retries
            attempts += 1
            try:
                return (
                    job.provider.generate(job.prompt, timeout=settings.timeout_seconds),
                    errors,
                    attempts,
                )
            except ProviderError as error:
                errors.append({"code": error.code, "message": str(error)})
                if not error.retryable:
                    break
            except Exception as error:  # provider bugs must not reach the game
                errors.append({"code": "provider_error", "message": type(error).__name__})
                break
            if attempt < settings.max_retries:
                self.sleep(self.retry_delay_seconds * (2 ** attempt))
        return None, errors, attempts

    def _generate(self, job: _Job) -> None:
        settings = job.prompt.settings
        started = self.clock()
        output, errors, attempts = self._call_provider(job, settings)
        text: str | None = None
        outcome = "generated"
        fallback_reason: str | None = None
        if output is not None:
            checked = review(output.text, language=settings.language,
                             maximum=job.prompt.max_length)
            text, fallback_reason = checked.text, checked.problem
            if output.truncated and text is not None and not checked.adjusted:
                # A cut-off answer that happens to fit is still incomplete.
                text, fallback_reason = None, "truncated"
        else:
            fallback_reason = errors[-1]["code"] if errors else "provider_error"
        if text is None:
            outcome = "fallback"
            try:
                text = template_commentary(
                    job.prompt.context, language=settings.language,
                    honorific=settings.use_names,
                )
            except Exception:  # pragma: no cover - the template only formats facts
                logging.getLogger("commentary").exception(
                    "template commentary failed", extra={"event": "commentary_failed"}
                )
                text, outcome = None, "error"
        duration_ms = round((self.clock() - started) * 1000, 3)
        finished = {
            "outcome": outcome,
            "response_text": output.text if output is not None else None,
            "final_text": text,
            "fallback_reason": fallback_reason if outcome != "generated" else None,
            "error_code": errors[-1]["code"] if errors else None,
            "error_message": errors[-1]["message"] if errors else None,
            "attempts": attempts,
            "errors": errors,
            "provider_request_ids": [output.request_id] if output and output.request_id else [],
            "model": output.model if output is not None else settings.model,
            "finished_at": _now(),
            "duration_ms": duration_ms,
        }
        logger = logging.getLogger("commentary")
        with self.game_service.lock, self._lock:
            if self._target != job.request_id:
                self.log.update(job.request_id, status="discarded", **finished)
                logger.info(
                    "stale commentary discarded",
                    extra={"event": "commentary_discarded", "gameId": job.game_id,
                           "turnId": job.turn_id, "requestId": job.request_id},
                )
                return
            self._displayed = job.request_id
            self.log.update(job.request_id, status="displayed", **finished)
            extra = {
                "gameId": job.game_id, "turnId": job.turn_id, "requestId": job.request_id,
                "provider": job.provider.name, "durationMs": duration_ms,
                "attempts": attempts,
            }
            if outcome == "generated":
                logger.info("commentary generated",
                            extra={"event": "commentary_generated", **extra})
            else:
                logger.warning(
                    "commentary replaced by the template",
                    extra={"event": "commentary_fallback", "errorCode": fallback_reason,
                           **extra},
                )
            base = {
                **_idle(),
                "gameId": job.game_id,
                "turnId": job.turn_id,
                "turnNumber": job.turn_number,
                "requestId": job.request_id,
                "revision": job.revision,
                "provider": job.provider.name,
                "model": finished["model"],
                "durationMs": duration_ms,
            }
            if text is None:
                self._set_state({**base, "status": "failed", "fallbackReason": fallback_reason})
                return
            self._set_state({
                **base,
                "status": "ready",
                "text": text,
                "source": outcome,
                "fallbackReason": fallback_reason if outcome == "fallback" else None,
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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _idle() -> dict[str, Any]:
    return {
        "status": "idle",
        "gameId": None,
        "turnId": None,
        "turnNumber": None,
        "requestId": None,
        "revision": None,
        "text": None,
        "source": None,
        "fallbackReason": None,
        "provider": None,
        "model": None,
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
