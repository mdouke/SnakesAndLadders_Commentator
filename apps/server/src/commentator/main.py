"""Application factory with an injectable repository."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from .api.errors import ApiError, install_error_handlers
from .api.responses import (
    REALTIME_MESSAGE_ADAPTER,
    BoardResponse,
    GameResponse,
    HealthResponse,
    RecognitionCamerasResponse,
    RecognitionResponse,
    RollCorrectionPreview,
)
from .api.schemas import (
    GameCreate,
    PositionCorrection,
    RecognitionCandidateConfirm,
    RecognitionCandidateReject,
    RecognitionStart,
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
from .services.game import GameService
from .services.realtime import IDLE_COMMENTARY, RealtimeClient, RealtimeHub
from .services.recognition import RecognitionRuntimeSettings, RecognitionService


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
    board: Board | None = None,
    repository: GameRepository | None = None,
    recognition_service: RecognitionService | None = None,
    realtime_hub: RealtimeHub | None = None,
) -> FastAPI:
    board = board if board is not None else load_board_preset()
    repo = repository if repository is not None else MemoryGameRepository()
    game_service = GameService(repo)
    recognition = recognition_service or _create_recognition_service(game_service)
    recognition.game_service = game_service
    hub = realtime_hub if realtime_hub is not None else RealtimeHub()
    game_service.add_listener(lambda payload: hub.publish("game", payload))
    recognition.subscribe(lambda payload: hub.publish("recognition", payload))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        with game_service.lock:
            recover_interrupted_games(repo)
        try:
            yield
        finally:
            if recognition.running:
                recognition.stop()

    application = FastAPI(
        title="Snakes & Ladders Commentator API", version="0.1.0", lifespan=lifespan
    )
    install_error_handlers(application)
    application.state.game_service = game_service
    application.state.recognition_service = recognition
    application.state.realtime_hub = hub
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

    def require_ended_confirmation(game: GameState, confirmed: bool) -> None:
        if game.status in {GameStatus.COMPLETED, GameStatus.ABORTED} and not confirmed:
            raise ApiError(
                409, "ended_game_confirmation_required",
                "終了したゲームの結果を変更するには確認が必要です",
            )

    @application.get("/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/boards/default", response_model=BoardResponse)
    def default_board() -> dict[str, Any]:
        return board.to_dict()

    @application.post("/api/games", status_code=201, response_model=GameResponse)
    def create_game(request: GameCreate) -> dict[str, Any]:
        with game_service.lock:
            game = GameState.create(board, [
                PlayerState(
                    id=str(uuid4()), name=item.name, color=item.color,
                    display_name=item.display_name, spoken_name=item.spoken_name,
                ) for item in request.players
            ])
            game_service.save_or_503(game, make_current=True)
            return game.to_dict()

    @application.get("/api/games", response_model=list[GameResponse])
    def list_games() -> list[dict[str, Any]]:
        with game_service.lock:
            return [game.to_dict() for game in repo.list()]

    @application.get("/api/games/current", response_model=GameResponse)
    def current_game() -> dict[str, Any]:
        with game_service.lock:
            game = repo.current()
            if game is None:
                raise ApiError(
                    404, "current_game_not_found", "ゲームがまだ作成されていません"
                )
            return game.to_dict()

    @application.get("/api/games/{game_id}", response_model=GameResponse)
    def get_game(game_id: str) -> dict[str, Any]:
        with game_service.lock:
            return game_service.game_or_404(game_id).to_dict()

    @application.post("/api/games/{game_id}/activate", response_model=GameResponse)
    def activate_game(game_id: str, request: VersionedRequest) -> dict[str, Any]:
        with game_service.lock:
            game = game_service.game_or_404(game_id)
            game_service.check_version(game, request.expected_state_version)
            game_service.set_current(game.id)
            return game.to_dict()

    @application.post("/api/games/{game_id}/rolls", response_model=GameResponse)
    def create_roll(game_id: str, request: RollCreate) -> dict[str, Any]:
        with game_service.lock:
            game = game_service.game_or_404(game_id)
            game_service.check_version(game, request.expected_state_version)
            game_service.check_turn(game, request.expected_turn_number)
            _apply_action(lambda: game.roll(request.value))
            game_service.save_or_503(game)
            result = game.to_dict()
        recognition.manual_committed(request.value)
        return result

    @application.post(
        "/api/games/{game_id}/position-corrections", response_model=GameResponse
    )
    def correct_position(game_id: str, request: PositionCorrection) -> dict[str, Any]:
        with game_service.lock:
            game = game_service.game_or_404(game_id)
            game_service.check_version(game, request.expected_state_version)
            require_ended_confirmation(game, request.confirm_ended_game)
            _apply_action(lambda: game.correct_position(
                request.player_id, request.position,
                reason=request.reason, operator=request.operator,
            ))
            game_service.save_or_503(game)
            return game.to_dict()

    @application.post(
        "/api/games/{game_id}/turns/{turn_id}/correction",
        response_model=GameResponse | RollCorrectionPreview,
    )
    def correct_roll(
        game_id: str, turn_id: str, request: RollCorrection
    ) -> dict[str, Any]:
        with game_service.lock:
            game = game_service.game_or_404(game_id)
            game_service.check_version(game, request.expected_state_version)
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
            game_service.save_or_503(game)
            return game.to_dict()

    @application.post("/api/games/{game_id}/restore", response_model=GameResponse)
    def restore_game(game_id: str, request: RestoreRequest) -> dict[str, Any]:
        with game_service.lock:
            game = game_service.game_or_404(game_id)
            game_service.check_version(game, request.expected_state_version)
            require_ended_confirmation(game, request.confirm_ended_game)
            snapshot = repo.snapshot(game_id, request.target_state_version)
            if snapshot is None:
                raise ApiError(
                    404, "snapshot_not_found", "スナップショットが見つかりません"
                )
            _apply_action(lambda: game.restore_from(snapshot))
            game_service.save_or_503(game)
            return game.to_dict()

    @application.post("/api/games/{game_id}/{action}", response_model=GameResponse)
    def change_state(
        game_id: str, action: str, request: VersionedRequest
    ) -> dict[str, Any]:
        with game_service.lock:
            game = game_service.game_or_404(game_id)
            game_service.check_version(game, request.expected_state_version)
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
            game_service.save_or_503(game)
            if action == "resume":
                game_service.set_current(game.id)
            return game.to_dict()

    @application.get("/api/recognition", response_model=RecognitionResponse)
    def recognition_state() -> dict[str, Any]:
        return recognition.snapshot()

    @application.get(
        "/api/recognition/cameras", response_model=RecognitionCamerasResponse
    )
    def recognition_cameras() -> dict[str, Any]:
        return {"cameras": recognition.cameras()}

    @application.post("/api/recognition/start", response_model=RecognitionResponse)
    def start_recognition(request: RecognitionStart) -> dict[str, Any]:
        return recognition.start(request.camera_id)

    @application.post("/api/recognition/stop", response_model=RecognitionResponse)
    def stop_recognition() -> dict[str, Any]:
        return recognition.stop()

    @application.post(
        "/api/recognition/candidate/confirm", response_model=GameResponse
    )
    def confirm_recognition_candidate(
        request: RecognitionCandidateConfirm,
    ) -> dict[str, Any]:
        return recognition.confirm_candidate(
            request.game_id,
            request.candidate_id,
            expected_state_version=request.expected_state_version,
            expected_turn_number=request.expected_turn_number,
            value=request.value,
        )

    @application.post(
        "/api/recognition/candidate/reject", response_model=RecognitionResponse
    )
    def reject_recognition_candidate(
        request: RecognitionCandidateReject,
    ) -> dict[str, Any]:
        return recognition.reject_candidate(request.candidate_id)

    @application.get(
        "/api/recognition/preview.jpg",
        response_class=Response,
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    def recognition_preview() -> Response:
        return Response(
            recognition.preview_jpeg(),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    def send_initial_state(client: RealtimeClient) -> None:
        # Each snapshot is queued under the lock that orders its later updates,
        # so a client never receives an older state after a newer one.
        with game_service.lock:
            hub.send_to(client, "game", game_service.current_payload())
        recognition.send_snapshot(
            lambda payload: hub.send_to(client, "recognition", payload)
        )
        hub.send_to(client, "commentary", dict(IDLE_COMMENTARY))

    @application.websocket("/ws")
    async def realtime(websocket: WebSocket) -> None:
        await websocket.accept()
        client = hub.connect()

        async def send_messages() -> None:
            while True:
                message = await client.queue.get()
                if message is None:
                    await websocket.close(code=1013)
                    return
                await websocket.send_json(message)

        async def wait_for_disconnect() -> None:
            while True:
                received = await websocket.receive()
                if received["type"] == "websocket.disconnect":
                    return

        tasks: set[asyncio.Task[None]] = set()
        try:
            await run_in_threadpool(send_initial_state, client)
            tasks = {
                asyncio.create_task(send_messages()),
                asyncio.create_task(wait_for_disconnect()),
            }
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    request_logger.info(
                        "websocket closed with error",
                        extra={"event": "websocket_closed_with_error"},
                    )
        finally:
            hub.disconnect(client)
            for task in tasks:
                task.cancel()

    def openapi_with_realtime_messages() -> dict[str, Any]:
        if application.openapi_schema is None:
            schema = get_openapi(
                title=application.title,
                version=application.version,
                routes=application.routes,
            )
            add_realtime_message_schemas(schema)
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = openapi_with_realtime_messages  # type: ignore[method-assign]
    return application


def add_realtime_message_schemas(schema: dict[str, Any]) -> None:
    """Publish the /ws message union, which OpenAPI cannot describe as a route."""
    message_schema = REALTIME_MESSAGE_ADAPTER.json_schema(
        by_alias=True,
        mode="serialization",
        ref_template="#/components/schemas/{model}",
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, definition in message_schema.pop("$defs", {}).items():
        existing = components.get(name)
        if existing is not None and existing != definition:
            raise RuntimeError(f"OpenAPI schema conflict for {name}")
        components[name] = definition
    components["RealtimeMessage"] = message_schema


def create_production_app() -> FastAPI:
    """Build the production app only when the ASGI server invokes its factory."""
    repository_root = Path(__file__).resolve().parents[4]
    configure_logging(load_log_config(default_directory=repository_root / "var" / "log"))
    database_path = Path(os.environ.get(
        "COMMENTATOR_DB_PATH", str(repository_root / "var" / "commentator.sqlite3")
    ))
    return create_app(repository=SQLiteGameRepository(database_path))


def _create_recognition_service(game_service: GameService) -> RecognitionService:
    """Load optional vision dependencies without making them server requirements."""
    repository_root = Path(__file__).resolve().parents[4]
    try:
        from .services.vision.camera import (
            CameraDisconnectedError,
            OpenCVCameraProvider,
        )
        from .services.vision.detector import DiceDetector
        from .services.vision.pipeline import DiceRecognitionPipeline
        from .services.vision.recognition import DiceRecognitionSession
        from .services.vision.settings import VisionSettingsStore

        settings = VisionSettingsStore(
            repository_root / "var" / "vision-settings.json"
        ).load()
        pipeline = DiceRecognitionPipeline(
            detector=DiceDetector(
                white_threshold=settings.white_threshold,
                pip_threshold=settings.pip_threshold,
            ),
            session=DiceRecognitionSession(settings.recognition_config()),
            roi=settings.roi,
        )
        return RecognitionService(
            game_service,
            pipeline=pipeline,
            camera_provider=OpenCVCameraProvider(),
            settings=RecognitionRuntimeSettings(
                camera_id=settings.camera_id,
                camera_width=settings.camera_width,
                camera_height=settings.camera_height,
                exposure=settings.exposure,
            ),
            camera_disconnected_errors=(CameraDisconnectedError,),
        )
    except Exception as error:
        logging.getLogger("commentator.recognition").warning(
            "recognition unavailable",
            extra={
                "event": "recognition_unavailable",
                "errorCode": "recognition_unavailable",
                "reason": type(error).__name__,
            },
        )
        return RecognitionService(
            game_service,
            pipeline=None,
            camera_provider=None,
            unavailable_reason=type(error).__name__,
        )

