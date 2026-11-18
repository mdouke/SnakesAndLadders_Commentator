"""Thread-safe application service for game reads and mutations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from threading import RLock
from typing import TYPE_CHECKING, Any

from ..api.errors import ApiError
from ..domain.game import GameState, GameStatus, OperationType, RollSource

if TYPE_CHECKING:
    from ..main import GameRepository
    from .vision.recognition import Confirmation


class GameService:
    """Own the repository lock shared by HTTP and recognition workers."""

    def __init__(self, repository: GameRepository) -> None:
        self.repository = repository
        self.lock = RLock()
        self._listeners: list[Callable[[dict[str, Any] | None], None]] = []
        self._last_published: tuple[str, int] | None = None

    def add_listener(self, listener: Callable[[dict[str, Any] | None], None]) -> None:
        """Receive the current game after each change, under the game lock.

        Listeners must not block or take the recognition lock (lock order is
        recognition -> game -> realtime hub).
        """
        with self.lock:
            self._listeners.append(listener)

    def current_payload(self) -> dict[str, Any] | None:
        with self.lock:
            game = self.repository.current()
            return game.to_dict() if game is not None else None

    def publish_current(self) -> None:
        """Notify listeners when the current game or its state version changed."""
        with self.lock:
            game = self.repository.current()
            key = (game.id, game.state_version) if game is not None else None
            if key == self._last_published:
                return
            self._last_published = key
            payload = game.to_dict() if game is not None else None
            for listener in tuple(self._listeners):
                try:
                    listener(payload)
                except Exception:
                    logging.getLogger("commentator.realtime").exception(
                        "game listener failed",
                        extra={"event": "game_listener_failed"},
                    )

    def set_current(self, game_id: str) -> None:
        with self.lock:
            self.repository.set_current(game_id)
            self.publish_current()

    def game_or_404(self, game_id: str) -> GameState:
        game = self.repository.get(game_id)
        if game is None:
            raise ApiError(404, "game_not_found", "ゲームが見つかりません")
        return game

    @staticmethod
    def check_version(game: GameState, expected: int) -> None:
        if expected != game.state_version:
            raise ApiError(
                409,
                "state_version_conflict",
                "ゲーム状態が更新されています",
                {
                    "expectedStateVersion": expected,
                    "actualStateVersion": game.state_version,
                },
            )

    @staticmethod
    def check_turn(game: GameState, expected: int | None) -> None:
        if expected is not None and expected != game.turn_number:
            raise ApiError(
                409,
                "turn_conflict",
                "手番が更新されています",
                {
                    "expectedTurnNumber": expected,
                    "actualTurnNumber": game.turn_number,
                },
            )

    def save_or_503(self, game: GameState, *, make_current: bool = False) -> None:
        try:
            if make_current:
                self.repository.save_and_set_current(game)
            else:
                self.repository.save(game)
        except Exception as error:
            logging.getLogger("commentator.persistence").exception(
                "game save failed",
                extra={
                    "event": "game_save_failed",
                    "gameId": game.id,
                    "stateVersion": game.state_version,
                    "errorCode": "game_save_failed",
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
        self.publish_current()

    def register_recognition_roll(
        self,
        confirmation: Confirmation,
        *,
        expected_game_id: str | None = None,
        expected_state_version: int | None = None,
        expected_turn_number: int | None = None,
    ) -> GameState:
        """Register one recognition result under the same lock as HTTP writes."""
        with self.lock:
            game = self.repository.current()
            if game is None:
                raise ApiError(
                    409, "game_not_in_progress", "進行中のゲームがありません"
                )
            if expected_game_id is not None and game.id != expected_game_id:
                raise ApiError(
                    409,
                    "current_game_conflict",
                    "対象のゲームが切り替わっています",
                    {
                        "expectedGameId": expected_game_id,
                        "actualGameId": game.id,
                    },
                )
            if game.status is not GameStatus.IN_PROGRESS:
                raise ApiError(
                    409,
                    "game_not_in_progress",
                    "進行中のゲームでのみサイコロを確定できます",
                    {"status": game.status.value},
                )
            if expected_state_version is not None:
                self.check_version(game, expected_state_version)
            self.check_turn(game, expected_turn_number)
            if any(
                event.type is OperationType.ROLL
                and event.payload.get("confirmationId") == confirmation.id
                for event in game.operation_events
            ):
                raise ApiError(
                    409,
                    "duplicate_confirmation",
                    "同じ認識確定はすでに登録されています",
                    {"confirmationId": confirmation.id},
                )
            confidence = (
                _confidence_payload(confirmation) if confirmation.confidence else None
            )
            try:
                game.roll(
                    confirmation.value,
                    source=RollSource(confirmation.source.value),
                    confirmation_id=confirmation.id,
                    original_candidate=confirmation.original_candidate,
                    confidence=confidence,
                )
            except (ValueError, RuntimeError) as error:
                raise ApiError(
                    409, "invalid_game_operation", str(error)
                ) from error
            self.save_or_503(game)
            return game

    def current_roll_context(self) -> tuple[str, int, int] | None:
        """Capture the optimistic-lock values used by an automatic confirmation."""
        with self.lock:
            game = self.repository.current()
            if game is None:
                return None
            return game.id, game.state_version, game.turn_number


def _confidence_payload(confirmation: Confirmation) -> dict[str, object]:
    assert confirmation.confidence is not None
    raw = asdict(confirmation.confidence)
    return {
        "detector": raw["detector"],
        "valueStability": raw["value_stability"],
        "stillness": raw["stillness"],
        "positionStability": raw["position_stability"],
        "total": raw["total"],
        "pipSizeConsistency": raw["pip_size_consistency"],
        "singlePipCenterOffset": raw["single_pip_center_offset"],
        "pipMergeSuspected": raw["pip_merge_suspected"],
        "pipRecounted": raw["pip_recounted"],
        "pipLayoutError": raw["pip_layout_error"],
    }
