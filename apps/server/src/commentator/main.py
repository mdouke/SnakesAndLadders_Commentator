"""FastAPI entry point for the initial manual-input vertical slice."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .api.schemas import GameCreate, RollCreate
from .domain.game import GameState, PlayerState
from .presets import load_board_preset

app = FastAPI(title="Snakes & Ladders Commentator API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_board = load_board_preset()
_games: dict[str, GameState] = {}
_latest_game_id: str | None = None


def _game_or_404(game_id: str) -> GameState:
    game = _games.get(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="ゲームが見つかりません")
    return game


def _apply_action(action: Any) -> None:
    try:
        action()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/boards/default")
def default_board() -> dict[str, Any]:
    return _board.to_dict()


@app.post("/api/games", status_code=201)
def create_game(request: GameCreate) -> dict[str, Any]:
    global _latest_game_id

    players = [
        PlayerState(
            id=str(uuid4()),
            name=item.name,
            color=item.color,
            display_name=item.display_name,
            spoken_name=item.spoken_name,
        )
        for item in request.players
    ]
    game = GameState.create(_board, players)
    _games[game.id] = game
    _latest_game_id = game.id
    return game.to_dict()


@app.get("/api/games/current")
def current_game() -> dict[str, Any]:
    if _latest_game_id is None:
        raise HTTPException(status_code=404, detail="ゲームがまだ作成されていません")
    return _game_or_404(_latest_game_id).to_dict()


@app.get("/api/games/{game_id}")
def get_game(game_id: str) -> dict[str, Any]:
    return _game_or_404(game_id).to_dict()


@app.post("/api/games/{game_id}/start")
def start_game(game_id: str) -> dict[str, Any]:
    game = _game_or_404(game_id)
    _apply_action(game.start)
    return game.to_dict()


@app.post("/api/games/{game_id}/rolls")
def create_roll(game_id: str, request: RollCreate) -> dict[str, Any]:
    game = _game_or_404(game_id)
    _apply_action(lambda: game.roll(request.value))
    return game.to_dict()


@app.post("/api/games/{game_id}/pause")
def pause_game(game_id: str) -> dict[str, Any]:
    game = _game_or_404(game_id)
    _apply_action(game.pause)
    return game.to_dict()


@app.post("/api/games/{game_id}/resume")
def resume_game(game_id: str) -> dict[str, Any]:
    game = _game_or_404(game_id)
    _apply_action(game.resume)
    return game.to_dict()


@app.post("/api/games/{game_id}/suspend")
def suspend_game(game_id: str) -> dict[str, Any]:
    game = _game_or_404(game_id)
    _apply_action(game.suspend)
    return game.to_dict()


@app.post("/api/games/{game_id}/abort")
def abort_game(game_id: str) -> dict[str, Any]:
    game = _game_or_404(game_id)
    _apply_action(game.abort)
    return game.to_dict()

