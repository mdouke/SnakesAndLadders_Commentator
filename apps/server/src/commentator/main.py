"""Application factory with an injectable repository."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .api.schemas import GameCreate, RollCreate
from .domain.board import Board
from .domain.game import GameState, PlayerState
from .presets import load_board_preset


class GameRepository(Protocol):
    def get(self, game_id: str) -> GameState | None: ...
    def save(self, game: GameState) -> None: ...
    def current(self) -> GameState | None: ...


@dataclass
class MemoryGameRepository:
    games: dict[str, GameState] = field(default_factory=dict)
    latest_game_id: str | None = None

    def get(self, game_id: str) -> GameState | None:
        return self.games.get(game_id)

    def save(self, game: GameState) -> None:
        self.games[game.id] = game
        self.latest_game_id = game.id

    def current(self) -> GameState | None:
        return self.get(self.latest_game_id) if self.latest_game_id else None


def _apply_action(action: Callable[[], object]) -> None:
    try:
        action()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def create_app(
    board: Board | None = None, repository: GameRepository | None = None
) -> FastAPI:
    board = board if board is not None else load_board_preset()
    repo = repository if repository is not None else MemoryGameRepository()
    lock = RLock()
    application = FastAPI(title="Snakes & Ladders Commentator API", version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
    )

    def game_or_404(game_id: str) -> GameState:
        game = repo.get(game_id)
        if game is None:
            raise HTTPException(status_code=404, detail="ゲームが見つかりません")
        return game

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
            repo.save(game)
            return game.to_dict()

    @application.get("/api/games/current")
    def current_game() -> dict[str, Any]:
        with lock:
            game = repo.current()
            if game is None:
                raise HTTPException(status_code=404, detail="ゲームがまだ作成されていません")
            return game.to_dict()

    @application.get("/api/games/{game_id}")
    def get_game(game_id: str) -> dict[str, Any]:
        with lock:
            return game_or_404(game_id).to_dict()

    @application.post("/api/games/{game_id}/rolls")
    def create_roll(game_id: str, request: RollCreate) -> dict[str, Any]:
        with lock:
            game = game_or_404(game_id)
            if request.expected_turn_number != game.turn_number:
                raise HTTPException(status_code=409, detail="手番が更新されています")
            _apply_action(lambda: game.roll(request.value))
            repo.save(game)
            return game.to_dict()

    @application.post("/api/games/{game_id}/{action}")
    def change_state(game_id: str, action: str) -> dict[str, Any]:
        with lock:
            game = game_or_404(game_id)
            actions: dict[str, Callable[[], object]] = {
                "start": game.start, "pause": game.pause, "resume": game.resume,
                "suspend": game.suspend, "abort": game.abort,
            }
            if action not in actions:
                raise HTTPException(status_code=404, detail="操作が見つかりません")
            _apply_action(actions[action])
            repo.save(game)
            return game.to_dict()

    return application


app = create_app()

