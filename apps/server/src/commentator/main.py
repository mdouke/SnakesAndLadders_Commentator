"""Application factory with an injectable repository."""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .api.errors import ApiError, install_error_handlers
from .api.schemas import (
    GameCreate,
    PositionCorrection,
    RestoreRequest,
    RollCorrection,
    RollCreate,
    VersionedRequest,
)
from .domain.board import Board
from .domain.game import (
    CorrectionConfirmationRequired,
    GameState,
    GameStatus,
    PlayerState,
)
from .infrastructure.logging import configure_logging, load_log_config
from .infrastructure.persistence import SQLiteGameRepository
from .presets import load_board_preset


class GameRepository(Protocol):
    def get(self, game_id: str) -> GameState | None: ...
    def save(self, game: GameState) -> None: ...
    def save_and_set_current(self, game: GameState) -> None: ...
    def current(self) -> GameState | None: ...
    def list(self) -> list[GameState]: ...
    def snapshot(self, game_id: str, state_version: int) -> GameState | None: ...
    def set_current(self, game_id: str) -> None: ...
    def last_saved_at(self, game_id: str) -> datetime | None: ...


@dataclass
class MemoryGameRepository:
    games: dict[str, GameState] = field(default_factory=dict)
    current_game_id: str | None = None
    snapshots: dict[tuple[str, int], GameState] = field(default_factory=dict)
    saved_at: dict[str, datetime] = field(default_factory=dict)

    def get(self, game_id: str) -> GameState | None:
        game = self.games.get(game_id)
        return deepcopy(game) if game is not None else None

    def save(self, game: GameState) -> None:
        self.games[game.id] = deepcopy(game)
        self.snapshots[(game.id, game.state_version)] = deepcopy(game)
        self.saved_at[game.id] = datetime.now(UTC)

    def save_and_set_current(self, game: GameState) -> None:
        self.save(game)
        self.current_game_id = game.id

    def current(self) -> GameState | None:
        return self.get(self.current_game_id) if self.current_game_id else None

    def list(self) -> list[GameState]:
        return [deepcopy(game) for game in reversed(self.games.values())]

    def snapshot(self, game_id: str, state_version: int) -> GameState | None:
        game = self.snapshots.get((game_id, state_version))
        return deepcopy(game) if game is not None else None

    def set_current(self, game_id: str) -> None:
        if game_id not in self.games:
            raise ValueError("ゲームが見つかりません")
        self.current_game_id = game_id

    def last_saved_at(self, game_id: str) -> datetime | None:
        return self.saved_at.get(game_id)


def recover_interrupted_games(repo: GameRepository) -> None:
    detected_at = datetime.now(UTC)
    for game in repo.list():
        if game.status is GameStatus.IN_PROGRESS:
            game.pause(
                now=detected_at,
                reason="process_interrupted",
                last_saved_at=repo.last_saved_at(game.id),
            )
            repo.save(game)
            logging.getLogger("commentator.recovery").warning(
                "interrupted game paused",
                extra={
                    "event": "game_interruption_detected",
                    "gameId": game.id,
                    "stateVersion": game.state_version,
                },
            )


def _apply_action(action: Callable[[], object]) -> None:
    try:
        action()
    except (ValueError, RuntimeError) as error:
        raise ApiError(409, "invalid_game_operation", str(error)) from error


def create_app(
    board: Board | None = None, repository: GameRepository | None = None,
) -> FastAPI:
    board = board if board is not None else load_board_preset()
    repo = repository if repository is not None else MemoryGameRepository()
    lock = RLock()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        with lock:
            recover_interrupted_games(repo)
        yield

    application = FastAPI(
        title="Snakes & Ladders Commentator API", version="0.1.0", lifespan=lifespan
    )
    install_error_handlers(application)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
    )
    request_logger = logging.getLogger("commentator.api")

    @application.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        started = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            request_logger.exception(
                "request failed",
                extra={
                    "event": "http_request_failed",
                    "requestId": request_id,
                    "durationMs": round((monotonic() - started) * 1000, 3),
                },
            )
            raise
        response.headers["X-Request-ID"] = request_id
        request_logger.info(
            "request completed",
            extra={
                "event": "http_request_completed",
                "requestId": request_id,
                "durationMs": round((monotonic() - started) * 1000, 3),
                "method": request.method,
                "path": request.url.path,
                "statusCode": response.status_code,
            },
        )
        return response

    def game_or_404(game_id: str) -> GameState:
        game = repo.get(game_id)
        if game is None:
            raise ApiError(404, "game_not_found", "ゲームが見つかりません")
        return game

    def check_version(game: GameState, expected: int) -> None:
        if expected != game.state_version:
            raise ApiError(
                409, "state_version_conflict", "ゲーム状態が更新されています",
                {"expectedStateVersion": expected, "actualStateVersion": game.state_version},
            )

    def save_or_503(game: GameState, *, make_current: bool = False) -> None:
        try:
            if make_current:
                repo.save_and_set_current(game)
            else:
                repo.save(game)
        except Exception as error:
            logging.getLogger("commentator.persistence").exception(
                "game save failed",
                extra={
                    "event": "game_save_failed", "gameId": game.id,
                    "stateVersion": game.state_version, "errorCode": "game_save_failed",
                },
            )
            raise ApiError(
                503, "game_save_failed", "ゲーム状態を保存できませんでした"
            ) from error
        logging.getLogger("commentator.game").info(
            "game state saved",
            extra={
                "event": "game_state_saved",
                "gameId": game.id,
                "stateVersion": game.state_version,
                "turnId": game.turns[-1].id if game.turns else None,
            },
        )

    def require_ended_confirmation(game: GameState, confirmed: bool) -> None:
        if game.status in {GameStatus.COMPLETED, GameStatus.ABORTED} and not confirmed:
            raise ApiError(
                409, "ended_game_confirmation_required",
                "終了したゲームの結果を変更するには確認が必要です",
            )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/boards/default")
    def default_board() -> dict[str, Any]:
        return board.to_dict()

    @application.post("/api/games", status_code=201)
    def create_game(request: GameCreate) -> dict[str, Any]:
        with lock:
            game = GameState.create(board, [
                PlayerState(
                    id=str(uuid4()), name=item.name, color=item.color,
                    display_name=item.display_name, spoken_name=item.spoken_name,
                ) for item in request.players
            ])
            save_or_503(game, make_current=True)
            return game.to_dict()

    @application.get("/api/games")
    def list_games() -> list[dict[str, Any]]:
        with lock:
            return [game.to_dict() for game in repo.list()]

    @application.get("/api/games/current")
    def current_game() -> dict[str, Any]:
        with lock:
            game = repo.current()
            if game is None:
                raise ApiError(
                    404, "current_game_not_found", "ゲームがまだ作成されていません"
                )
            return game.to_dict()

    @application.get("/api/games/{game_id}")
    def get_game(game_id: str) -> dict[str, Any]:
        with lock:
            return game_or_404(game_id).to_dict()

    @application.post("/api/games/{game_id}/activate")
    def activate_game(game_id: str, request: VersionedRequest) -> dict[str, Any]:
        with lock:
            game = game_or_404(game_id)
            check_version(game, request.expected_state_version)
            repo.set_current(game.id)
            return game.to_dict()

    @application.post("/api/games/{game_id}/rolls")
    def create_roll(game_id: str, request: RollCreate) -> dict[str, Any]:
        with lock:
            game = game_or_404(game_id)
            check_version(game, request.expected_state_version)
            if (
                request.expected_turn_number is not None
                and request.expected_turn_number != game.turn_number
            ):
                raise ApiError(409, "turn_conflict", "手番が更新されています")
            _apply_action(lambda: game.roll(request.value))
            save_or_503(game)
            return game.to_dict()

    @application.post("/api/games/{game_id}/position-corrections")
    def correct_position(game_id: str, request: PositionCorrection) -> dict[str, Any]:
        with lock:
            game = game_or_404(game_id)
            check_version(game, request.expected_state_version)
            require_ended_confirmation(game, request.confirm_ended_game)
            _apply_action(lambda: game.correct_position(
                request.player_id, request.position,
                reason=request.reason, operator=request.operator,
            ))
            save_or_503(game)
            return game.to_dict()

    @application.post("/api/games/{game_id}/turns/{turn_id}/correction")
    def correct_roll(
        game_id: str, turn_id: str, request: RollCorrection
    ) -> dict[str, Any]:
        with lock:
            game = game_or_404(game_id)
            check_version(game, request.expected_state_version)
            require_ended_confirmation(game, request.confirm_ended_game)
            if request.dry_run:
                try:
                    invalidated = game.preview_roll_correction(turn_id, request.value)
                except (ValueError, RuntimeError) as error:
                    raise ApiError(
                        409, "roll_correction_failed", str(error)
                    ) from error
                return {
                    "requiresConfirmation": invalidated.requires_confirmation,
                    "invalidatedTurns": invalidated.turns,
                    "invalidatedOperations": invalidated.operations,
                }
            try:
                game.correct_roll(
                    turn_id, request.value, reason=request.reason, operator=request.operator,
                    confirm_invalidated_rolls=request.confirm_invalidated_rolls,
                )
            except CorrectionConfirmationRequired as error:
                raise ApiError(
                    409, "roll_invalidation_confirmation_required",
                    "後続操作の無効化には確認が必要です",
                    {
                        "invalidatedTurns": error.invalidated_turns,
                        "invalidatedOperations": error.invalidated_operations,
                    },
                ) from error
            except (ValueError, RuntimeError) as error:
                raise ApiError(409, "roll_correction_failed", str(error)) from error
            save_or_503(game)
            return game.to_dict()

    @application.post("/api/games/{game_id}/restore")
    def restore_game(game_id: str, request: RestoreRequest) -> dict[str, Any]:
        with lock:
            game = game_or_404(game_id)
            check_version(game, request.expected_state_version)
            require_ended_confirmation(game, request.confirm_ended_game)
            snapshot = repo.snapshot(game_id, request.target_state_version)
            if snapshot is None:
                raise ApiError(
                    404, "snapshot_not_found", "スナップショットが見つかりません"
                )
            _apply_action(lambda: game.restore_from(snapshot))
            save_or_503(game)
            return game.to_dict()

    @application.post("/api/games/{game_id}/{action}")
    def change_state(
        game_id: str, action: str, request: VersionedRequest
    ) -> dict[str, Any]:
        with lock:
            game = game_or_404(game_id)
            check_version(game, request.expected_state_version)
            actions: dict[str, Callable[[], object]] = {
                "start": game.start, "pause": game.pause, "resume": game.resume,
                "suspend": game.suspend, "abort": game.abort,
                "undo": game.undo, "redo": game.redo,
            }
            if action not in actions:
                raise ApiError(404, "game_action_not_found", "操作が見つかりません")
            if action in {"undo", "redo"}:
                require_ended_confirmation(game, request.confirm_ended_game)
            _apply_action(actions[action])
            save_or_503(game)
            if action == "resume":
                repo.set_current(game.id)
            return game.to_dict()

    return application


def create_production_app() -> FastAPI:
    """Build the production app only when the ASGI server invokes its factory."""
    repository_root = Path(__file__).resolve().parents[4]
    configure_logging(load_log_config(default_directory=repository_root / "var" / "log"))
    database_path = Path(os.environ.get(
        "COMMENTATOR_DB_PATH", str(repository_root / "var" / "commentator.sqlite3")
    ))
    return create_app(repository=SQLiteGameRepository(database_path))

