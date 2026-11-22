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
from typing import Any, Literal, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from .api.errors import ApiError, install_error_handlers
from .api.responses import (
    REALTIME_MESSAGE_ADAPTER,
    BoardPresetList,
    BoardResponse,
    BoardValidation,
    CommentaryPromptPreview,
    CommentaryRequestList,
    CommentaryResponse,
    CommentarySettingsResponse,
    GameResponse,
    HealthResponse,
    LoggingStatus,
    RecognitionCamerasResponse,
    RecognitionDiagnostics,
    RecognitionResponse,
    RollCorrectionPreview,
    SpeechResponse,
    SpeechSettingsResponse,
    SystemStatus,
    VisionSettingsResponse,
    VlmReadingList,
    VlmReadingRecord,
    VlmSettingsResponse,
    VoiceList,
)
from .api.schemas import (
    BoardDefinition,
    CommentaryRegenerate,
    CommentarySettingsUpdate,
    GameCreate,
    LoggingSettingsUpdate,
    PositionCorrection,
    RecognitionCandidateConfirm,
    RecognitionCandidateReject,
    RecognitionStart,
    RestoreRequest,
    ResumeFromTurn,
    RollCorrection,
    RollCreate,
    SpeechPreview,
    SpeechSettingsUpdate,
    TurnChange,
    VersionedRequest,
    VisionSettingsRevert,
    VisionSettingsUpdate,
    VlmSettingsUpdate,
)
from .domain.board import Board
from .domain.game import (
    CorrectionConfirmationRequired,
    GameState,
    GameStatus,
    PlayerState,
)
from .infrastructure.logging import LoggingControl
from .infrastructure.persistence import SQLiteGameRepository
from .infrastructure.persistence.commentary_log import SQLiteCommentaryLog, to_api
from .infrastructure.secrets import default_secrets_path, load_secrets
from .presets import BoardPresetError, BoardPresetStore, board_from_definition, load_board_preset
from .services.commentary import CommentaryError, CommentaryService
from .services.game import GameService
from .services.google_tts import GeminiTtsEngine, GoogleTtsEngine
from .services.llm.prompts import version_list
from .services.llm.providers import provider_availability
from .services.llm.settings import CommentarySettingsStore
from .services.realtime import RealtimeClient, RealtimeHub
from .services.recognition import RecognitionRuntimeSettings, RecognitionService
from .services.tts import (
    GEMINI_TTS_MODELS,
    GEMINI_TTS_VOICES,
    MacSayEngine,
    SpeechService,
    SpeechSettings,
    SpeechSettingsStore,
)


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
    commentary_service: CommentaryService | None = None,
    board_store: BoardPresetStore | None = None,
    logging_control: LoggingControl | None = None,
    speech_service: SpeechService | None = None,
) -> FastAPI:
    board = board if board is not None else load_board_preset()
    boards = board_store if board_store is not None else BoardPresetStore.default()
    log_control = logging_control if logging_control is not None else LoggingControl.unmanaged()

    def board_error(error: BoardPresetError) -> ApiError:
        status = {"board_not_found": 404, "invalid_board": 422}.get(error.code, 409)
        return ApiError(status, error.code, str(error))
    repo = repository if repository is not None else MemoryGameRepository()
    game_service = GameService(repo)
    recognition = recognition_service or _create_recognition_service(game_service)
    recognition.game_service = game_service
    hub = realtime_hub if realtime_hub is not None else RealtimeHub()
    game_service.add_listener(lambda payload: hub.publish("game", payload))
    recognition.subscribe(lambda payload: hub.publish("recognition", payload))
    recognition.attach_game()
    commentary = commentary_service or CommentaryService(game_service)
    commentary.game_service = game_service
    commentary.subscribe(lambda payload: hub.publish("commentary", payload))
    # Registered after the hub listener, so each game update is sent before the
    # commentary request it triggers.
    commentary.attach()

    def player_readings() -> dict[str, str]:
        game = game_service.current_payload()
        if game is None:
            return {}
        return {
            player["displayName"]: player["spokenName"]
            for player in game["players"]
            if player.get("spokenName")
        }

    speech = speech_service or SpeechService()
    speech.is_current = commentary.is_current
    speech.readings = player_readings
    speech.language = lambda: commentary.settings.get().language
    # After the hub subscriber, so the screen shows a comment before it is spoken.
    commentary.subscribe(speech.commentary_changed)
    speech.subscribe(lambda payload: hub.publish("speech", payload))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log_control.start()
        with game_service.lock:
            recover_interrupted_games(repo)
        speech.start()
        try:
            yield
        finally:
            if recognition.running:
                recognition.stop()
            speech.stop()
            log_control.stop()

    application = FastAPI(
        title="Snakes & Ladders Commentator API", version="0.1.0", lifespan=lifespan
    )
    install_error_handlers(application)
    application.state.game_service = game_service
    application.state.recognition_service = recognition
    application.state.realtime_hub = hub
    application.state.commentary_service = commentary
    application.state.speech_service = speech
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

    def require_not_aborted(game: GameState) -> None:
        if game.status is GameStatus.ABORTED:
            raise ApiError(
                409, "game_aborted",
                "中止したゲームは、中止を取り消してから操作してください",
            )

    def require_ended_confirmation(game: GameState, confirmed: bool) -> None:
        require_not_aborted(game)
        if game.status is GameStatus.COMPLETED and not confirmed:
            raise ApiError(
                409, "ended_game_confirmation_required",
                "終了したゲームの結果を変更するには確認が必要です",
            )

    @application.get("/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/boards", response_model=BoardPresetList)
    def list_boards() -> dict[str, Any]:
        return {"presets": [
            {
                "id": item.id, "name": item.name, "builtIn": item.built_in,
                "snakeCount": item.snake_count, "ladderCount": item.ladder_count,
            }
            for item in boards.list()
        ]}

    @application.post("/api/boards/validate", response_model=BoardValidation)
    def validate_board(request: BoardDefinition) -> dict[str, Any]:
        try:
            board_from_definition(request.to_board_dict())
        except BoardPresetError as error:
            return {"valid": False, "message": str(error)}
        return {"valid": True, "message": None}

    @application.get("/api/boards/{board_id}", response_model=BoardResponse)
    def get_board(board_id: str) -> dict[str, Any]:
        if board_id == "default":
            return board.to_dict()
        try:
            return boards.get(board_id).to_dict()
        except BoardPresetError as error:
            raise board_error(error) from error

    @application.put("/api/boards/{board_id}", response_model=BoardResponse)
    def save_board(board_id: str, request: BoardDefinition) -> dict[str, Any]:
        if request.id != board_id:
            raise ApiError(422, "invalid_board", "盤面IDがURLと一致しません")
        try:
            return boards.save(request.to_board_dict()).to_dict()
        except BoardPresetError as error:
            raise board_error(error) from error

    @application.delete("/api/boards/{board_id}", status_code=204, response_class=Response)
    def delete_board(board_id: str) -> Response:
        try:
            boards.delete(board_id)
        except BoardPresetError as error:
            raise board_error(error) from error
        return Response(status_code=204)

    @application.post("/api/games", status_code=201, response_model=GameResponse)
    def create_game(request: GameCreate) -> dict[str, Any]:
        try:
            game_board = boards.get(request.board_id) if request.board_id else board
        except BoardPresetError as error:
            raise board_error(error) from error
        with game_service.lock:
            game = GameState.create(game_board, [
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

    @application.post("/api/games/{game_id}/turn-change", response_model=GameResponse)
    def change_turn(game_id: str, request: TurnChange) -> dict[str, Any]:
        with game_service.lock:
            game = game_service.game_or_404(game_id)
            game_service.check_version(game, request.expected_state_version)
            require_not_aborted(game)
            _apply_action(lambda: game.change_turn(
                request.player_id, reason=request.reason, operator=request.operator,
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

    @application.post("/api/games/{game_id}/resume-from-turn", response_model=GameResponse)
    def resume_from_turn(game_id: str, request: ResumeFromTurn) -> dict[str, Any]:
        with game_service.lock:
            game = game_service.game_or_404(game_id)
            game_service.check_version(game, request.expected_state_version)
            require_ended_confirmation(game, request.confirm_ended_game)
            _apply_action(lambda: game.resume_after_turn(
                request.turn_id, reason=request.reason, operator=request.operator,
            ))
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
                "undo": game.undo, "redo": game.redo, "reopen": game.reopen,
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
    def recognition_preview(
        view: Literal["overlay", "mask", "pips"] = "overlay",
    ) -> Response:
        return Response(
            recognition.preview_jpeg(view),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @application.get(
        "/api/recognition/diagnostics", response_model=RecognitionDiagnostics
    )
    def recognition_diagnostics() -> dict[str, Any]:
        return recognition.diagnostics()

    @application.get("/api/settings/vlm", response_model=VlmSettingsResponse)
    def get_vlm_settings() -> dict[str, Any]:
        return recognition.vlm_settings_payload()

    @application.put("/api/settings/vlm", response_model=VlmSettingsResponse)
    def update_vlm_settings(request: VlmSettingsUpdate) -> dict[str, Any]:
        changes = request.model_dump(exclude_unset=True, by_alias=True)
        if any(value is None for value in changes.values()):
            raise ApiError(422, "invalid_settings", "設定値に null は指定できません")
        return recognition.update_vlm_settings(changes)

    @application.post("/api/recognition/vlm/test", response_model=VlmReadingRecord)
    def test_vlm_reading() -> dict[str, Any]:
        return recognition.test_reading()

    @application.get("/api/recognition/vlm/readings", response_model=VlmReadingList)
    def vlm_readings() -> dict[str, Any]:
        return {"readings": recognition.recent_readings()}

    @application.get("/api/settings/vision", response_model=VisionSettingsResponse)
    def get_vision_settings() -> dict[str, Any]:
        return recognition.vision_settings_payload()

    @application.put("/api/settings/vision", response_model=VisionSettingsResponse)
    def update_vision_settings(request: VisionSettingsUpdate) -> dict[str, Any]:
        changes = request.model_dump(exclude_unset=True, by_alias=True)
        for key in ("roi", "cameraResolution"):
            if key in changes and changes[key] is None:
                raise ApiError(422, "invalid_settings", f"{key} に null は指定できません")
            if isinstance(changes.get(key), dict):
                changes[key] = {k: v for k, v in changes[key].items() if v is not None}
        nullable = {"cameraId", "whiteThreshold", "pipThreshold", "exposure", "roi",
                    "cameraResolution"}
        for key, value in changes.items():
            if value is None and key not in nullable:
                raise ApiError(422, "invalid_settings", f"{key} に null は指定できません")
        return recognition.update_vision_settings(changes)

    @application.post("/api/settings/vision/save", response_model=VisionSettingsResponse)
    def save_vision_settings() -> dict[str, Any]:
        return recognition.save_vision_settings()

    @application.post(
        "/api/settings/vision/revert", response_model=VisionSettingsResponse
    )
    def revert_vision_settings(request: VisionSettingsRevert) -> dict[str, Any]:
        return recognition.revert_vision_settings(defaults=request.target == "defaults")

    def system_status() -> dict[str, Any]:
        database: dict[str, Any]
        health = getattr(repo, "health", None)
        try:
            if callable(health):
                health()
                database = {"status": "ok", "label": "SQLite", "detail": None}
            else:
                database = {
                    "status": "warning", "label": "メモリ",
                    "detail": "再起動するとゲームが消えます（テスト用の保存先）",
                }
        except Exception as error:
            database = {"status": "error", "label": "SQLite", "detail": type(error).__name__}

        snapshot = recognition.snapshot()
        if not snapshot["available"]:
            camera = {"status": "not_configured", "label": "利用不可",
                      "detail": "OpenCVなどの追加パッケージがありません"}
        elif not snapshot["running"]:
            camera = {"status": "off", "label": "停止中", "detail": snapshot["errorCode"]}
        elif snapshot["errorCode"] in {"camera_disconnected", "recognition_worker_failed"}:
            camera = {"status": "error", "label": "エラー", "detail": snapshot["errorCode"]}
        else:
            camera = {
                "status": "ok",
                "label": "認識中" if snapshot["gameReady"] else "映像のみ（ゲーム進行中でない）",
                "detail": snapshot["state"],
            }

        comment = commentary.snapshot()
        comment_settings = commentary.settings.get()
        provider_label = {
            "fake": "テンプレート（仮の実況）",
            "claude": f"Claude（{comment_settings.claude_model}）",
            "openai": f"OpenAI（{comment_settings.openai_model}）",
        }[comment_settings.provider]
        commentary_status: dict[str, Any]
        if not comment_settings.enabled:
            commentary_status = {"status": "off", "label": "オフ", "detail": None}
        elif comment["status"] == "failed":
            commentary_status = {"status": "error", "label": provider_label,
                                 "detail": "直近の実況を作れませんでした"}
        elif comment["source"] == "fallback":
            commentary_status = {
                "status": "warning", "label": provider_label,
                "detail": f"直近の実況は代替文です（{comment['fallbackReason']}）",
            }
        else:
            commentary_status = {"status": "ok", "label": provider_label, "detail": None}
        speech_settings = speech.settings.get()
        speech_state = speech.snapshot()
        tts_status: dict[str, Any]
        if not speech.engine.available():
            tts_status = {"status": "not_configured", "label": "利用不可",
                          "detail": "この環境では読み上げを使えません"}
        elif not speech_settings.enabled:
            tts_status = {"status": "off", "label": "オフ", "detail": None}
        elif speech_state["status"] == "error":
            tts_status = {"status": "error", "label": "エラー", "detail": speech_state["error"]}
        else:
            tts_status = {
                "status": "ok",
                "label": "読み上げ中" if speech_state["status"] == "speaking" else "待機中",
                "detail": (
                    f"Gemini（{speech_settings.gemini_voice}）"
                    if speech_settings.provider == "gemini"
                    else f"Google（{speech_settings.google_voice}）"
                    if speech_settings.provider == "google"
                    else speech_settings.voice or "標準の音声"
                ),
            }
        return {
            "checkedAt": datetime.now(UTC).isoformat(),
            "database": database,
            "camera": camera,
            "commentary": commentary_status,
            "tts": tts_status,
            "recording": {
                "status": "not_configured", "label": "未接続", "detail": "Phase 7 で接続予定",
            },
            "realtimeClients": hub.client_count,
            "logging": log_control.status(),
            "recentProblems": log_control.problems.items(),
        }

    @application.get("/api/status", response_model=SystemStatus)
    def get_status() -> dict[str, Any]:
        return system_status()

    @application.put("/api/settings/logging", response_model=LoggingStatus)
    def update_logging(request: LoggingSettingsUpdate) -> dict[str, Any]:
        try:
            log_control.set_retention(request.retention)
        except PermissionError as error:
            raise ApiError(409, "logging_settings_locked", str(error)) from error
        logging.getLogger("commentator.settings").info(
            "log retention changed",
            extra={"event": "log_retention_changed", "retention": request.retention},
        )
        return log_control.status()

    def commentary_settings_payload() -> dict[str, Any]:
        return {
            **commentary.settings.get().to_dict(),
            "providers": provider_availability(),
            "promptVersions": version_list(),
        }

    @application.get("/api/commentary", response_model=CommentaryResponse)
    def get_commentary() -> dict[str, Any]:
        return commentary.snapshot()

    @application.post("/api/commentary/regenerate", response_model=CommentaryResponse)
    def regenerate_commentary(request: CommentaryRegenerate) -> dict[str, Any]:
        try:
            return commentary.regenerate(request.turn_id)
        except CommentaryError as error:
            raise ApiError(409, error.code, str(error)) from error

    @application.get("/api/commentary/requests", response_model=CommentaryRequestList)
    def list_commentary_requests(
        game_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        rows = commentary.log.list(game_id=game_id, limit=max(1, min(limit, 200)))
        return {"requests": [to_api(row) for row in rows]}

    @application.get("/api/commentary/prompt", response_model=CommentaryPromptPreview)
    def get_commentary_prompt() -> dict[str, Any]:
        return commentary.prompt_preview()

    @application.get("/api/settings/commentary", response_model=CommentarySettingsResponse)
    def get_commentary_settings() -> dict[str, Any]:
        return commentary_settings_payload()

    @application.put("/api/settings/commentary", response_model=CommentarySettingsResponse)
    def update_commentary_settings(request: CommentarySettingsUpdate) -> dict[str, Any]:
        changes = request.model_dump(by_alias=True, exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise ApiError(422, "invalid_settings", "設定値に null は指定できません")
        try:
            commentary.settings.update(changes)
        except ValueError as error:
            raise ApiError(422, "invalid_settings", str(error)) from error
        commentary.settings_changed()
        logging.getLogger("commentator.settings").info(
            "commentary settings changed",
            extra={"event": "commentary_settings_changed", "fields": sorted(changes)},
        )
        return commentary_settings_payload()

    def speech_settings_payload() -> dict[str, Any]:
        google = speech.engine_for("google")
        key_configured = getattr(google, "key_configured", None)
        gemini = speech.engine_for("gemini")
        gemini_key = getattr(gemini, "key_configured", None)
        gemini_cloud = getattr(gemini, "cloud_configured", None)
        return {
            **speech.settings.get().to_dict(),
            "available": speech.engine.available(),
            "engine": speech.engine.name,
            "geminiModels": {
                backend: list(models) for backend, models in GEMINI_TTS_MODELS.items()
            },
            "geminiVoices": [
                {"name": name, "gender": gender} for name, gender in GEMINI_TTS_VOICES.items()
            ],
            "providers": {
                "gemini": "gemini" in speech.engines and gemini.available(),
                "geminiKeyConfigured": bool(gemini_key()) if callable(gemini_key) else False,
                "geminiCloudConfigured": bool(gemini_cloud()) if callable(gemini_cloud)
                else False,
                "macos": speech.engine_for("macos").available(),
                "google": "google" in speech.engines and google.available(),
                "googleKeyConfigured": bool(key_configured()) if callable(key_configured)
                else False,
            },
        }

    @application.get("/api/speech", response_model=SpeechResponse)
    def get_speech() -> dict[str, Any]:
        return speech.snapshot()

    @application.get("/api/speech/voices", response_model=VoiceList)
    def list_voices(
        provider: Literal["macos", "google", "gemini"] | None = None,
    ) -> dict[str, Any]:
        engine = speech.engine_for(provider) if provider else speech.engine
        return {"voices": [voice.to_dict() for voice in engine.voices()]}

    @application.get("/api/settings/speech", response_model=SpeechSettingsResponse)
    def get_speech_settings() -> dict[str, Any]:
        return speech_settings_payload()

    @application.put("/api/settings/speech", response_model=SpeechSettingsResponse)
    def update_speech_settings(request: SpeechSettingsUpdate) -> dict[str, Any]:
        changes = request.model_dump(by_alias=True, exclude_unset=True)
        not_nullable = ("enabled", "volume", "provider", "googleVoice", "googleSpeakingRate",
                        "geminiBackend", "geminiModel", "geminiVoice", "geminiStyle")
        if any(key in changes and changes[key] is None for key in not_nullable):
            raise ApiError(422, "invalid_settings",
                           "voice と rate 以外の設定に null は指定できません")
        try:
            speech.settings.update(changes)
        except ValueError as error:
            raise ApiError(422, "invalid_settings", str(error)) from error
        speech.settings_changed()
        return speech_settings_payload()

    @application.post("/api/speech/preview", response_model=SpeechResponse)
    def preview_speech(request: SpeechPreview) -> dict[str, Any]:
        base = speech.settings.get()
        given = request.model_dump(exclude_unset=True)
        try:
            settings = SpeechSettings(
                enabled=True,
                voice=given["voice"] if "voice" in given else base.voice,
                rate=given["rate"] if "rate" in given else base.rate,
                volume=given["volume"] if given.get("volume") is not None else base.volume,
                provider=given.get("provider") or base.provider,
                google_voice=given.get("google_voice") or base.google_voice,
                google_speaking_rate=given.get("google_speaking_rate")
                or base.google_speaking_rate,
                gemini_backend=given.get("gemini_backend") or base.gemini_backend,
                gemini_model=given.get("gemini_model") or base.gemini_model,
                gemini_voice=given.get("gemini_voice") or base.gemini_voice,
                gemini_style=(
                    given["gemini_style"] if given.get("gemini_style") is not None
                    else base.gemini_style
                ),
            ).validated()
            speech.preview(request.text, settings)
        except ValueError as error:
            raise ApiError(422, "invalid_settings", str(error)) from error
        except RuntimeError as error:
            raise ApiError(409, "speech_unavailable", str(error)) from error
        return speech.snapshot()

    @application.post("/api/speech/stop", response_model=SpeechResponse)
    def stop_speech() -> dict[str, Any]:
        speech.cancel()
        return speech.snapshot()

    def send_initial_state(client: RealtimeClient) -> None:
        # Each snapshot is queued under the lock that orders its later updates,
        # so a client never receives an older state after a newer one.
        with game_service.lock:
            hub.send_to(client, "game", game_service.current_payload())
        recognition.send_snapshot(
            lambda payload: hub.send_to(client, "recognition", payload)
        )
        commentary.send_snapshot(
            lambda payload: hub.send_to(client, "commentary", payload)
        )
        speech.send_snapshot(lambda payload: hub.send_to(client, "speech", payload))

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
                error = None if task.cancelled() else task.exception()
                if error is not None:
                    # Usually the browser reloaded or closed the tab mid-send. It is
                    # not a server fault, so record the type without alarming wording.
                    request_logger.info(
                        "websocket connection ended abruptly",
                        extra={
                            "event": "websocket_ended_abruptly",
                            "errorType": type(error).__name__,
                        },
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
    log_control = LoggingControl(
        settings_path=repository_root / "var" / "log-settings.json",
        default_directory=repository_root / "var" / "log",
        managed=True,
    )
    log_control.start()  # before anything else logs
    # API keys for the commentary providers; `export` in the terminal still wins.
    load_secrets(default_secrets_path(repository_root))
    database_path = Path(os.environ.get(
        "COMMENTATOR_DB_PATH", str(repository_root / "var" / "commentator.sqlite3")
    ))
    repository = SQLiteGameRepository(database_path)
    game_service = GameService(repository)
    commentary = CommentaryService(
        game_service,
        settings=CommentarySettingsStore(repository_root / "var" / "commentary-settings.json"),
        log=SQLiteCommentaryLog(database_path),
    )
    speech = SpeechService(
        engines={
            "macos": MacSayEngine(), "google": GoogleTtsEngine(), "gemini": GeminiTtsEngine(),
        },
        settings=SpeechSettingsStore(repository_root / "var" / "speech-settings.json"),
    )
    return create_app(
        repository=repository,
        commentary_service=commentary,
        logging_control=log_control,
        speech_service=speech,
    )


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
        from .services.vision.vlm import ClaudeDiceReader, VlmSettingsStore

        store = VisionSettingsStore(repository_root / "var" / "vision-settings.json")
        settings = store.load()
        pipeline = DiceRecognitionPipeline(
            detector=DiceDetector(
                white_threshold=settings.white_threshold,
                pip_threshold=settings.pip_threshold,
                debug_pips=True,  # for the "pips" view of the tuning dialog
            ),
            session=DiceRecognitionSession(settings.recognition_config()),
            roi=settings.roi,
            tray_detection=settings.tray_detection,
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
            vision_settings=settings,
            settings_store=store,
            dice_reader=ClaudeDiceReader(),
            vlm_settings=VlmSettingsStore(repository_root / "var" / "vlm-settings.json"),
            image_dir=repository_root / "recordings" / "vlm",
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

