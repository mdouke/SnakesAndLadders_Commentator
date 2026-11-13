"""Deterministic game state and turn processing."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from .board import Board, Jump, JumpType


class GameStatus(StrEnum):
    READY = "ready"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    ABORTED = "aborted"


class EventType(StrEnum):
    NORMAL_MOVE = "normal_move"
    LARGE_ROLL = "large_roll"
    SMALL_ROLL = "small_roll"
    GOAL_OVERSHOOT = "goal_overshoot"
    LADDER = "ladder"
    SNAKE = "snake"
    GOAL = "goal"
    NEAR_GOAL = "near_goal"


class OperationType(StrEnum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    SUSPEND = "suspend"
    ABORT = "abort"
    ROLL = "roll"
    POSITION_CORRECTION = "position_correction"
    CORRECTION = "correction"
    UNDO = "undo"
    REDO = "redo"
    RESTORE = "restore"


class CorrectionConfirmationRequired(ValueError):
    def __init__(
        self,
        invalidated_turns: list[dict[str, Any]],
        invalidated_operations: list[dict[str, Any]],
    ) -> None:
        super().__init__("後続ターンの無効化には確認が必要です")
        self.invalidated_turns = invalidated_turns
        self.invalidated_operations = invalidated_operations


@dataclass(frozen=True, slots=True)
class CorrectionInvalidations:
    turns: list[dict[str, Any]]
    operations: list[dict[str, Any]]

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.turns or self.operations)


@dataclass(slots=True)
class OperationRecord:
    id: str
    sequence: int
    timeline_index: int | None
    type: OperationType
    payload: dict[str, Any]
    occurred_at: datetime
    branch_id: str
    state_version: int
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sequence": self.sequence,
            "timelineIndex": self.timeline_index,
            "type": self.type.value,
            "payload": deepcopy(self.payload),
            "occurredAt": self.occurred_at.isoformat(),
            "branchId": self.branch_id,
            "stateVersion": self.state_version,
            "active": self.active,
        }


@dataclass(slots=True)
class HistoryAction:
    id: str
    label: str
    activated_event_ids: list[str]
    deactivated_event_ids: list[str]
    branch_before: str
    branch_after: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "activatedEventIds": list(self.activated_event_ids),
            "deactivatedEventIds": list(self.deactivated_event_ids),
            "branchBefore": self.branch_before,
            "branchAfter": self.branch_after,
        }


@dataclass(frozen=True, slots=True)
class JumpHistory:
    turn_number: int
    start: int
    end: int


@dataclass(slots=True)
class PlayerStatistics:
    rolls: list[int] = field(default_factory=list)
    face_counts: Counter[int] = field(default_factory=Counter)
    normal_movement_total: int = 0
    actual_position_change_total: int = 0
    overshoot_count: int = 0
    ladder_count: int = 0
    ladder_distance_total: int = 0
    ladder_history: list[JumpHistory] = field(default_factory=list)
    snake_count: int = 0
    snake_distance_total: int = 0
    snake_history: list[JumpHistory] = field(default_factory=list)
    maximum_position: int = 0

    @property
    def average_roll(self) -> float:
        return sum(self.rolls) / len(self.rolls) if self.rolls else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rolls": list(self.rolls),
            "faceCounts": {str(face): self.face_counts[face] for face in range(1, 7)},
            "averageRoll": self.average_roll,
            "normalMovementTotal": self.normal_movement_total,
            "actualPositionChangeTotal": self.actual_position_change_total,
            "overshootCount": self.overshoot_count,
            "ladderCount": self.ladder_count,
            "ladderDistanceTotal": self.ladder_distance_total,
            "snakeCount": self.snake_count,
            "snakeDistanceTotal": self.snake_distance_total,
            "maximumPosition": self.maximum_position,
        }


@dataclass(slots=True)
class PlayerState:
    id: str
    name: str
    color: str
    display_name: str | None = None
    spoken_name: str | None = None
    position: int = 0
    rank: int | None = None
    rank_confirmed: bool = False
    reached_goal: bool = False
    reached_goal_at: datetime | None = None
    statistics: PlayerStatistics = field(default_factory=PlayerStatistics)

    def __post_init__(self) -> None:
        if self.display_name is None:
            self.display_name = self.name
        if self.spoken_name is None:
            self.spoken_name = self.display_name

    def to_dict(
        self, *, is_current: bool, goal: int, minimum_rolls: int, winning_rolls: list[int]
    ) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "displayName": self.display_name,
            "spokenName": self.spoken_name,
            "color": self.color,
            "position": self.position,
            "rank": self.rank,
            "rankConfirmed": self.rank_confirmed,
            "reachedGoal": self.reached_goal,
            "reachedGoalAt": self.reached_goal_at.isoformat() if self.reached_goal_at else None,
            "remainingSquares": goal - self.position,
            "winningRolls": winning_rolls,
            "isCurrent": is_current,
            "theoreticalMinimumRolls": minimum_rolls,
            "statistics": self.statistics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TurnRecord:
    id: str
    number: int
    player_id: str
    roll: int
    before: int
    normal_position: int
    final_position: int
    overshoot: bool
    jump_type: JumpType | None
    events: tuple[EventType, ...]
    occurred_at: datetime
    branch_id: str = "main"
    state_version_before: int = 0
    state_version_after: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "playerId": self.player_id,
            "roll": self.roll,
            "before": self.before,
            "normalPosition": self.normal_position,
            "finalPosition": self.final_position,
            "overshoot": self.overshoot,
            "jumpType": self.jump_type.value if self.jump_type else None,
            "events": list(self.events),
            "occurredAt": self.occurred_at.isoformat(),
            "branchId": self.branch_id,
            "stateVersionBefore": self.state_version_before,
            "stateVersionAfter": self.state_version_after,
        }


@dataclass(slots=True)
class GameState:
    id: str
    board: Board
    players: list[PlayerState]
    branch_id: str = "main"
    suspension_history: list[datetime] = field(default_factory=list)
    status: GameStatus = GameStatus.READY
    current_player_index: int | None = 0
    turn_number: int = 0
    turns: list[TurnRecord] = field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    suspended_at: datetime | None = None
    state_version: int = 0
    operation_events: list[OperationRecord] = field(default_factory=list)
    undo_history: list[HistoryAction] = field(default_factory=list)
    redo_history: list[HistoryAction] = field(default_factory=list)
    interruption_detected_at: datetime | None = None
    interruption_last_saved_at: datetime | None = None
    initial_players: list[PlayerState] | None = None

    def __post_init__(self) -> None:
        if len(self.players) < 2:
            raise ValueError("プレイヤーは2人以上必要です")
        if len(self.players) > 6:
            raise ValueError("プレイヤーは6人以下にしてください")
        ids = [player.id for player in self.players]
        if len(ids) != len(set(ids)):
            raise ValueError("プレイヤーIDが重複しています")
        colors = [player.color.casefold() for player in self.players]
        if len(colors) != len(set(colors)):
            raise ValueError("プレイヤーの色は重複できません")
        if self.initial_players is None:
            self.initial_players = [
                PlayerState(
                    id=player.id,
                    name=player.name,
                    color=player.color,
                    display_name=player.display_name,
                    spoken_name=player.spoken_name,
                    position=player.position,
                )
                for player in self.players
            ]
        self._refresh_ranks()

    @property
    def current_player(self) -> PlayerState | None:
        if self.current_player_index is None:
            return None
        return self.players[self.current_player_index]

    @classmethod
    def create(
        cls,
        board: Board,
        players: list[PlayerState],
        *,
        game_id: str | None = None,
    ) -> GameState:
        return cls(id=game_id or str(uuid4()), board=board, players=players)

    def start(self, *, now: datetime | None = None) -> None:
        occurred_at = now or datetime.now(UTC)
        branch_before = self.branch_id
        branch_after = self._next_action_branch()
        self._apply_start(occurred_at)
        self._record(
            OperationType.START, {}, occurred_at, label="ゲーム開始",
            branch_before=branch_before, branch_after=branch_after,
        )

    def _apply_start(self, occurred_at: datetime) -> None:
        if self.status is not GameStatus.READY:
            raise ValueError("準備中のゲームだけを開始できます")
        self.status = GameStatus.IN_PROGRESS
        self.started_at = occurred_at

    def roll(self, value: int, *, now: datetime | None = None) -> TurnRecord:
        occurred_at = now or datetime.now(UTC)
        branch_before = self.branch_id
        branch_after = self._next_action_branch()
        player = self.current_player
        if player is None:
            raise ValueError("現在の手番がありません")
        turn = self._apply_roll(value, occurred_at, branch_after)
        self._record(
            OperationType.ROLL,
            {"value": value, "turnId": turn.id, "playerId": player.id},
            occurred_at,
            label=f"出目 {value}（ターン{turn.number}）",
            branch_before=branch_before,
            branch_after=branch_after,
        )
        return turn

    def _apply_roll(
        self, value: int, occurred_at: datetime, branch_id: str
    ) -> TurnRecord:
        if self.status is not GameStatus.IN_PROGRESS:
            raise ValueError("進行中のゲームでのみサイコロを確定できます")
        player = self.current_player
        if player is None or player.reached_goal:
            raise ValueError("現在の手番が不正です")

        result = self.board.move(player.position, value)
        self.turn_number += 1
        events = self._events_for(result.overshoot, result.jump, result.final_position, value)
        turn = TurnRecord(
            id=str(uuid5(NAMESPACE_URL, f"{self.id}/{branch_id}/turn/{self.turn_number}")),
            number=self.turn_number,
            player_id=player.id,
            roll=value,
            before=result.before,
            normal_position=result.normal_position,
            final_position=result.final_position,
            overshoot=result.overshoot,
            jump_type=result.jump.type if result.jump else None,
            events=events,
            occurred_at=occurred_at,
            branch_id=branch_id,
            state_version_before=self.state_version,
            state_version_after=self.state_version + 1,
        )
        self.turns.append(turn)
        player.statistics = compute_statistics(
            [item for item in self.turns if item.player_id == player.id]
        )
        player.position = result.final_position

        if player.position == self.board.goal:
            player.reached_goal = True
            player.rank_confirmed = True
            player.reached_goal_at = occurred_at
            player.rank = sum(other.reached_goal for other in self.players)

        if sum(item.reached_goal for item in self.players) >= len(self.players) - 1:
            self._complete(occurred_at)
        else:
            self._advance_turn()
            self._refresh_ranks()
        return turn

    def pause(
        self, *, now: datetime | None = None, reason: str | None = None,
        last_saved_at: datetime | None = None,
    ) -> None:
        occurred_at = now or datetime.now(UTC)
        branch_before = self.branch_id
        branch_after = self._next_action_branch()
        self._apply_pause()
        payload: dict[str, Any] = {}
        if reason is not None:
            payload["reason"] = reason
        if reason == "process_interrupted":
            self.interruption_detected_at = occurred_at
            self.interruption_last_saved_at = last_saved_at
            payload["detectedAt"] = occurred_at.isoformat()
            payload["lastSavedAt"] = last_saved_at.isoformat() if last_saved_at else None
        self._record(
            OperationType.PAUSE, payload, occurred_at,
            label="異常終了による一時停止" if reason else "一時停止",
            branch_before=branch_before, branch_after=branch_after,
        )

    def _apply_pause(self) -> None:
        if self.status is not GameStatus.IN_PROGRESS:
            raise ValueError("進行中のゲームだけを一時停止できます")
        self.status = GameStatus.PAUSED

    def resume(self, *, now: datetime | None = None) -> None:
        occurred_at = now or datetime.now(UTC)
        branch_before = self.branch_id
        branch_after = self._next_action_branch()
        self._apply_resume()
        self._record(
            OperationType.RESUME, {}, occurred_at, label="ゲーム再開",
            branch_before=branch_before, branch_after=branch_after,
        )

    def _apply_resume(self) -> None:
        if self.status not in {GameStatus.PAUSED, GameStatus.SUSPENDED}:
            raise ValueError("一時停止または保存終了したゲームだけを再開できます")
        self.status = GameStatus.IN_PROGRESS
        self.interruption_detected_at = None
        self.interruption_last_saved_at = None

    def suspend(self, *, now: datetime | None = None) -> None:
        occurred_at = now or datetime.now(UTC)
        branch_before = self.branch_id
        branch_after = self._next_action_branch()
        self._apply_suspend(occurred_at)
        self._record(
            OperationType.SUSPEND, {}, occurred_at, label="保存して終了",
            branch_before=branch_before, branch_after=branch_after,
        )

    def _apply_suspend(self, occurred_at: datetime) -> None:
        if self.status not in {GameStatus.IN_PROGRESS, GameStatus.PAUSED}:
            raise ValueError("進行中または一時停止中のゲームだけを保存終了できます")
        self.status = GameStatus.SUSPENDED
        self.suspended_at = occurred_at
        self.suspension_history.append(self.suspended_at)

    def abort(self, *, now: datetime | None = None) -> None:
        occurred_at = now or datetime.now(UTC)
        branch_before = self.branch_id
        branch_after = self._next_action_branch()
        self._apply_abort(occurred_at)
        self._record(
            OperationType.ABORT, {}, occurred_at, label="ゲーム中止",
            branch_before=branch_before, branch_after=branch_after,
        )

    def _apply_abort(self, occurred_at: datetime) -> None:
        if self.status in {GameStatus.COMPLETED, GameStatus.ABORTED}:
            raise ValueError("終了済みのゲームは中止できません")
        self.status = GameStatus.ABORTED
        self.ended_at = occurred_at
        self.current_player_index = None
        for player in self.players:
            player.rank_confirmed = False

    def correct_position(
        self, player_id: str, position: int, *, reason: str, operator: str,
        now: datetime | None = None,
    ) -> None:
        occurred_at = now or datetime.now(UTC)
        player = next((item for item in self.players if item.id == player_id), None)
        if player is None:
            raise ValueError("プレイヤーが見つかりません")
        before_position = player.position
        branch_before = self.branch_id
        branch_after = self._next_action_branch()
        self._apply_position_correction(player_id, position)
        self._record(
            OperationType.POSITION_CORRECTION,
            {
                "playerId": player_id,
                "beforePosition": before_position,
                "position": position,
                "reason": reason,
                "operator": operator,
            },
            occurred_at,
            label=f"位置訂正（{player.display_name}: {before_position}→{position}）",
            branch_before=branch_before,
            branch_after=branch_after,
        )

    def _apply_position_correction(self, player_id: str, position: int) -> None:
        if self.status not in {
            GameStatus.IN_PROGRESS, GameStatus.PAUSED,
            GameStatus.COMPLETED, GameStatus.ABORTED,
        }:
            raise ValueError("開始後のゲームだけ位置を訂正できます")
        if not 0 <= position < self.board.goal:
            if position == self.board.goal:
                raise ValueError("位置訂正ではゴールに移動できません。出目を訂正してください")
            raise ValueError("訂正位置が盤面の範囲外です")
        player = next((item for item in self.players if item.id == player_id), None)
        if player is None:
            raise ValueError("プレイヤーが見つかりません")
        was_goal = player.reached_goal
        was_completed = self.status is GameStatus.COMPLETED
        was_aborted = self.status is GameStatus.ABORTED
        player.position = position
        player.reached_goal = False
        player.reached_goal_at = None
        player.rank_confirmed = False
        if was_completed and was_goal:
            self.status = GameStatus.IN_PROGRESS
            self.ended_at = None
            self.current_player_index = self._next_player_index_from_history()
            for item in self.players:
                if not item.reached_goal:
                    item.rank_confirmed = False
        elif was_completed:
            self.status = GameStatus.COMPLETED
            self.current_player_index = None
            player.rank_confirmed = True
        elif was_aborted:
            self.status = GameStatus.ABORTED
            self.current_player_index = None
        self._refresh_ranks()

    def correct_roll(
        self, turn_id: str, value: int, *, reason: str, operator: str,
        now: datetime | None = None, branch_id: str | None = None,
        confirm_invalidated_rolls: bool = False,
    ) -> None:
        working = deepcopy(self)
        invalidated = working._correct_roll_in_place(
            turn_id, value, reason=reason, operator=operator,
            now=now, branch_id=branch_id,
        )
        if invalidated.requires_confirmation and not confirm_invalidated_rolls:
            raise CorrectionConfirmationRequired(
                invalidated.turns, invalidated.operations
            )
        self._adopt(working)

    def preview_roll_correction(
        self, turn_id: str, value: int
    ) -> CorrectionInvalidations:
        working = deepcopy(self)
        return working._correct_roll_in_place(
            turn_id, value, reason="dry_run", operator="dry_run"
        )

    def _correct_roll_in_place(
        self, turn_id: str, value: int, *, reason: str, operator: str,
        now: datetime | None = None, branch_id: str | None = None,
    ) -> CorrectionInvalidations:
        target_turn = next((turn for turn in self.turns if turn.id == turn_id), None)
        if target_turn is None:
            raise ValueError("訂正対象のターンが見つかりません")
        active = self._active_timeline()
        target = next(
            (
                event for event in active
                if event.type is OperationType.ROLL
                and event.payload.get("turnId") == target_turn.id
            ),
            None,
        )
        if target is None:
            raise ValueError("訂正対象の操作が見つかりません")
        if int(target.payload["value"]) == value:
            raise ValueError("同じ出目には訂正できません")
        corrected_result = self.board.move(target_turn.before, value)
        if (
            target_turn.final_position == self.board.goal
            and corrected_result.final_position != self.board.goal
        ):
            raise ValueError(
                "この訂正でゴールが取り消され、後続の手番が変わります。"
                "後続のターンをUndoしてから訂正してください"
            )
        occurred_at = now or datetime.now(UTC)
        branch_before = self.branch_id
        command_version = self.state_version
        new_branch = branch_id or str(uuid4())
        suffix = [
            event for event in active
            if event.timeline_index is not None
            and target.timeline_index is not None
            and event.timeline_index >= target.timeline_index
        ]
        turn_by_id = {turn.id: turn for turn in self.turns}
        activated: list[str] = []
        invalidated_turns: list[dict[str, Any]] = []
        invalidated_operations: list[dict[str, Any]] = []
        for event in suffix:
            event.active = False
        self._rebuild()
        for event in suffix:
            payload = deepcopy(event.payload)
            if event.id == target.id:
                payload["value"] = value
                payload.pop("turnId", None)
            elif self.status is GameStatus.COMPLETED:
                if event.type is OperationType.ROLL:
                    original_turn = turn_by_id.get(str(payload.get("turnId")))
                    invalidated_turns.append({
                        "eventId": event.id,
                        "turnId": payload.get("turnId"),
                        "turnNumber": (
                            original_turn.number if original_turn else event.timeline_index
                        ),
                        "playerId": str(payload.get("playerId", "")),
                        "value": int(payload["value"]),
                        "reason": "game_completed",
                    })
                else:
                    invalidated_operations.append({
                        "eventId": event.id,
                        "timelineIndex": event.timeline_index,
                        "type": event.type.value,
                        "reason": "game_completed",
                    })
                continue
            elif event.type is OperationType.POSITION_CORRECTION:
                corrected_player_id = str(payload.get("playerId", ""))
                corrected_player = next(
                    (
                        player for player in self.players
                        if player.id == corrected_player_id
                    ),
                    None,
                )
                if corrected_player is not None and corrected_player.reached_goal:
                    invalidated_operations.append({
                        "eventId": event.id,
                        "timelineIndex": event.timeline_index,
                        "type": event.type.value,
                        "playerId": corrected_player_id,
                        "position": int(payload["position"]),
                        "reason": "player_already_finished",
                    })
                    continue
            if event.type is OperationType.ROLL and event.id != target.id:
                expected_player_id = str(payload.get("playerId", ""))
                expected_player = next(
                    (player for player in self.players if player.id == expected_player_id), None
                )
                invalidation_reason: str | None = None
                if self.status is GameStatus.COMPLETED:
                    invalidation_reason = "game_completed"
                elif expected_player is not None and expected_player.reached_goal:
                    invalidation_reason = "player_already_finished"
                if invalidation_reason is not None:
                    original_turn = turn_by_id.get(str(payload.get("turnId")))
                    invalidated_turns.append({
                        "eventId": event.id,
                        "turnId": payload.get("turnId"),
                        "turnNumber": (
                            original_turn.number if original_turn else event.timeline_index
                        ),
                        "playerId": expected_player_id,
                        "value": int(payload["value"]),
                        "reason": invalidation_reason,
                    })
                    continue
            cloned = self._append_record(
                event.type, payload, event.occurred_at, branch_id=new_branch,
                timeline_index=event.timeline_index,
                record_state_version=event.state_version,
            )
            self._apply_record(cloned)
            activated.append(cloned.id)
        self.branch_id = new_branch
        self.state_version = command_version
        audit = self._append_record(
            OperationType.CORRECTION,
            {
                "turnId": turn_id, "beforeValue": target_turn.roll, "value": value,
                "reason": reason, "operator": operator,
                "invalidatedRolls": deepcopy(invalidated_turns),
                "invalidatedOperations": deepcopy(invalidated_operations),
            },
            occurred_at, branch_id=new_branch, timeline_index=None, active=False,
        )
        self.state_version += 1
        self.redo_history.clear()
        self.undo_history.append(HistoryAction(
            id=audit.id,
            label=f"出目訂正（ターン{target_turn.number}: {target_turn.roll}→{value}）",
            activated_event_ids=activated,
            deactivated_event_ids=[event.id for event in suffix],
            branch_before=branch_before,
            branch_after=new_branch,
        ))
        return CorrectionInvalidations(invalidated_turns, invalidated_operations)

    def _adopt(self, other: GameState) -> None:
        self.players = other.players
        self.branch_id = other.branch_id
        self.suspension_history = other.suspension_history
        self.status = other.status
        self.current_player_index = other.current_player_index
        self.turn_number = other.turn_number
        self.turns = other.turns
        self.started_at = other.started_at
        self.ended_at = other.ended_at
        self.suspended_at = other.suspended_at
        self.state_version = other.state_version
        self.operation_events = other.operation_events
        self.undo_history = other.undo_history
        self.redo_history = other.redo_history
        self.interruption_detected_at = other.interruption_detected_at
        self.interruption_last_saved_at = other.interruption_last_saved_at

    def undo(self, *, now: datetime | None = None) -> None:
        if not self.undo_history:
            raise ValueError("取り消せる操作がありません")
        action = self.undo_history.pop()
        self._set_events_active(action.activated_event_ids, False)
        self._set_events_active(action.deactivated_event_ids, True)
        self.branch_id = action.branch_before
        self._rebuild()
        occurred_at = now or datetime.now(UTC)
        self._append_record(
            OperationType.UNDO, {"targetActionId": action.id, "label": action.label}, occurred_at,
            timeline_index=None, active=False,
        )
        self.state_version += 1
        self.redo_history.append(action)

    def redo(self, *, now: datetime | None = None) -> None:
        if not self.redo_history:
            raise ValueError("やり直せる操作がありません")
        action = self.redo_history.pop()
        self._set_events_active(action.deactivated_event_ids, False)
        self._set_events_active(action.activated_event_ids, True)
        self.branch_id = action.branch_after
        self._rebuild()
        occurred_at = now or datetime.now(UTC)
        self._append_record(
            OperationType.REDO, {"targetActionId": action.id, "label": action.label}, occurred_at,
            timeline_index=None, active=False,
        )
        self.state_version += 1
        self.undo_history.append(action)

    def restore_from(self, snapshot: GameState, *, now: datetime | None = None) -> None:
        if snapshot.id != self.id:
            raise ValueError("別のゲームのスナップショットは復元できません")
        target_version = snapshot.state_version
        target_active_ids = {
            event.id for event in snapshot.operation_events
            if event.active and event.timeline_index is not None
        }
        current_active_ids = {event.id for event in self._active_timeline()}
        for event in self.operation_events:
            if event.timeline_index is not None:
                event.active = event.id in target_active_ids
        branch_before = self.branch_id
        self.branch_id = str(uuid4())
        self._rebuild()
        occurred_at = now or datetime.now(UTC)
        audit = self._append_record(
            OperationType.RESTORE, {"targetStateVersion": target_version}, occurred_at,
            branch_id=self.branch_id, timeline_index=None, active=False,
        )
        self.state_version += 1
        self.redo_history.clear()
        self.undo_history.append(HistoryAction(
            id=audit.id,
            label=f"状態復元（バージョン{target_version}）",
            activated_event_ids=sorted(target_active_ids - current_active_ids),
            deactivated_event_ids=sorted(current_active_ids - target_active_ids),
            branch_before=branch_before,
            branch_after=self.branch_id,
        ))

    def rankings(self) -> dict[str, int]:
        goal_turns = {
            player.id: min(
                (
                    turn.number for turn in self.turns
                    if turn.player_id == player.id and turn.final_position == self.board.goal
                ),
                default=10**9,
            )
            for player in self.players if player.reached_goal
        }
        goal_players = sorted(
            (player for player in self.players if player.reached_goal),
            key=lambda player: (goal_turns[player.id], self.players.index(player)),
        )
        result = {player.id: rank for rank, player in enumerate(goal_players, start=1)}
        active = sorted(
            (player for player in self.players if not player.reached_goal),
            key=lambda player: player.position,
            reverse=True,
        )
        completed_count = len(result)
        previous_position: int | None = None
        previous_rank: int | None = None
        for index, player in enumerate(active, start=completed_count + 1):
            if player.position == previous_position and previous_rank is not None:
                rank = previous_rank
            else:
                rank = index
            result[player.id] = rank
            previous_position = player.position
            previous_rank = rank
        return result

    def _next_player_index_from_history(self) -> int:
        if self.turns:
            last_player_id = self.turns[-1].player_id
            start = next(
                index for index, player in enumerate(self.players)
                if player.id == last_player_id
            )
        else:
            start = len(self.players) - 1
        for offset in range(1, len(self.players) + 1):
            candidate = (start + offset) % len(self.players)
            if not self.players[candidate].reached_goal:
                return candidate
        raise ValueError("次のプレイヤーが見つかりません")

    def to_dict(self) -> dict[str, Any]:
        current_id = self.current_player.id if self.current_player else None
        return {
            "id": self.id,
            "boardId": self.board.id,
            "branchId": self.branch_id,
            "stateVersion": self.state_version,
            "canUndo": bool(self.undo_history),
            "canRedo": bool(self.redo_history),
            "undoLabel": self.undo_history[-1].label if self.undo_history else None,
            "redoLabel": self.redo_history[-1].label if self.redo_history else None,
            "interruption": (
                {
                    "detectedAt": self.interruption_detected_at.isoformat(),
                    "lastSavedAt": (
                        self.interruption_last_saved_at.isoformat()
                        if self.interruption_last_saved_at else None
                    ),
                }
                if self.interruption_detected_at else None
            ),
            "suspensionHistory": [value.isoformat() for value in self.suspension_history],
            "status": self.status.value,
            "turnNumber": self.turn_number,
            "currentPlayerId": current_id,
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "endedAt": self.ended_at.isoformat() if self.ended_at else None,
            "suspendedAt": self.suspended_at.isoformat() if self.suspended_at else None,
            "players": [
                player.to_dict(
                    is_current=player.id == current_id,
                    goal=self.board.goal,
                    minimum_rolls=self.board.minimum_rolls_from(player.position),
                    winning_rolls=self.board.winning_rolls(player.position),
                )
                for player in self.players
            ],
            "turns": [turn.to_dict() for turn in self.turns],
        }

    def _record(
        self, operation_type: OperationType, payload: dict[str, Any], occurred_at: datetime,
        *, label: str, branch_before: str, branch_after: str,
    ) -> None:
        record = self._append_record(
            operation_type, payload, occurred_at, branch_id=branch_after
        )
        self.branch_id = branch_after
        self.state_version += 1
        self.redo_history.clear()
        self.undo_history.append(HistoryAction(
            id=record.id,
            label=label,
            activated_event_ids=[record.id],
            deactivated_event_ids=[],
            branch_before=branch_before,
            branch_after=branch_after,
        ))

    def _next_action_branch(self) -> str:
        return str(uuid4()) if self.redo_history else self.branch_id

    def _set_events_active(self, event_ids: list[str], active: bool) -> None:
        selected = set(event_ids)
        for event in self.operation_events:
            if event.id in selected:
                event.active = active

    def _append_record(
        self, operation_type: OperationType, payload: dict[str, Any], occurred_at: datetime,
        *, branch_id: str | None = None, timeline_index: int | None = None,
        active: bool = True, record_state_version: int | None = None,
    ) -> OperationRecord:
        sequence = max((event.sequence for event in self.operation_events), default=0) + 1
        if timeline_index is None and active:
            timeline_index = max(
                (event.timeline_index or 0 for event in self.operation_events if event.active),
                default=0,
            ) + 1
        selected_branch = branch_id or self.branch_id
        record = OperationRecord(
            id=str(uuid5(NAMESPACE_URL, f"{self.id}/{selected_branch}/event/{sequence}")),
            sequence=sequence,
            timeline_index=timeline_index,
            type=operation_type,
            payload=deepcopy(payload),
            occurred_at=occurred_at,
            branch_id=selected_branch,
            state_version=(
                record_state_version if record_state_version is not None
                else self.state_version + 1
            ),
            active=active,
        )
        self.operation_events.append(record)
        return record

    def _active_timeline(self) -> list[OperationRecord]:
        return sorted(
            (
                event for event in self.operation_events
                if event.active and event.timeline_index is not None
            ),
            key=lambda event: event.timeline_index or 0,
        )

    def _rebuild(self) -> None:
        assert self.initial_players is not None
        rebuilt = GameState(
            id=self.id,
            board=self.board,
            players=deepcopy(self.initial_players),
            branch_id=self.branch_id,
            initial_players=deepcopy(self.initial_players),
        )
        for event in self._active_timeline():
            rebuilt._apply_record(event)
        self.players = rebuilt.players
        self.status = rebuilt.status
        self.current_player_index = rebuilt.current_player_index
        self.turn_number = rebuilt.turn_number
        self.turns = rebuilt.turns
        self.started_at = rebuilt.started_at
        self.ended_at = rebuilt.ended_at
        self.suspended_at = rebuilt.suspended_at
        self.suspension_history = rebuilt.suspension_history
        self.interruption_detected_at = rebuilt.interruption_detected_at
        self.interruption_last_saved_at = rebuilt.interruption_last_saved_at

    def _apply_record(self, event: OperationRecord) -> None:
        self.state_version = max(0, event.state_version - 1)
        if event.type is OperationType.START:
            self._apply_start(event.occurred_at)
        elif event.type is OperationType.PAUSE:
            self._apply_pause()
            if event.payload.get("reason") == "process_interrupted":
                self.interruption_detected_at = datetime.fromisoformat(
                    str(event.payload["detectedAt"])
                )
                last_saved = event.payload.get("lastSavedAt")
                self.interruption_last_saved_at = (
                    datetime.fromisoformat(str(last_saved)) if last_saved else None
                )
        elif event.type is OperationType.RESUME:
            self._apply_resume()
        elif event.type is OperationType.SUSPEND:
            self._apply_suspend(event.occurred_at)
        elif event.type is OperationType.ABORT:
            self._apply_abort(event.occurred_at)
        elif event.type is OperationType.ROLL:
            expected_player = event.payload.get("playerId")
            current_player = self.current_player
            if expected_player is not None and (
                current_player is None or current_player.id != expected_player
            ):
                turn_number = self.turn_number + 1
                raise ValueError(
                    f"訂正後の手番と後続の出目が一致しません（ターン{turn_number}）"
                )
            turn = self._apply_roll(int(event.payload["value"]), event.occurred_at, event.branch_id)
            event.payload["turnId"] = turn.id
        elif event.type is OperationType.POSITION_CORRECTION:
            self._apply_position_correction(
                str(event.payload["playerId"]), int(event.payload["position"])
            )
        self.state_version = event.state_version

    def _refresh_ranks(self) -> None:
        for player in self.players:
            player.rank = self.rankings()[player.id]

    def _advance_turn(self) -> None:
        if self.current_player_index is None:
            raise RuntimeError("現在の手番がありません")
        for offset in range(1, len(self.players) + 1):
            candidate = (self.current_player_index + offset) % len(self.players)
            if not self.players[candidate].reached_goal:
                self.current_player_index = candidate
                return
        raise RuntimeError("次のプレイヤーが見つかりません")

    def _complete(self, occurred_at: datetime) -> None:
        self.status = GameStatus.COMPLETED
        self.ended_at = occurred_at
        self.current_player_index = None
        remaining = [player for player in self.players if not player.reached_goal]
        if len(remaining) != 1:
            raise RuntimeError("正常終了時には未ゴールのプレイヤーが1人必要です")
        last = remaining[0]
        last.rank = len(self.players)
        last.rank_confirmed = True

    def _events_for(
        self,
        overshoot: bool,
        jump: Jump | None,
        final_position: int,
        value: int,
    ) -> tuple[EventType, ...]:
        events = [EventType.GOAL_OVERSHOOT if overshoot else EventType.NORMAL_MOVE]
        if value >= 5:
            events.append(EventType.LARGE_ROLL)
        elif value <= 2:
            events.append(EventType.SMALL_ROLL)
        if jump is not None:
            events.append(EventType.LADDER if jump.type is JumpType.LADDER else EventType.SNAKE)
        if final_position == self.board.goal:
            events.append(EventType.GOAL)
        elif self.board.goal - final_position <= 6:
            events.append(EventType.NEAR_GOAL)
        return tuple(events)


def compute_statistics(turns: list[TurnRecord]) -> PlayerStatistics:
    """Derive one player's statistics solely from their effective turn history."""
    stats = PlayerStatistics()
    for turn in turns:
        stats.rolls.append(turn.roll)
        stats.face_counts[turn.roll] += 1
        stats.normal_movement_total += 0 if turn.overshoot else turn.roll
        stats.actual_position_change_total += abs(turn.final_position - turn.before)
        stats.overshoot_count += int(turn.overshoot)
        stats.maximum_position = max(
            stats.maximum_position, turn.before, turn.normal_position, turn.final_position
        )
        history = JumpHistory(turn.number, turn.normal_position, turn.final_position)
        if turn.jump_type is JumpType.LADDER:
            stats.ladder_count += 1
            stats.ladder_distance_total += turn.final_position - turn.normal_position
            stats.ladder_history.append(history)
        elif turn.jump_type is JumpType.SNAKE:
            stats.snake_count += 1
            stats.snake_distance_total += turn.normal_position - turn.final_position
            stats.snake_history.append(history)
    return stats


@dataclass(frozen=True, slots=True)
class RollInput:
    value: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OperationInput:
    type: OperationType
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)


def replay(
    initial: GameState, rolls: list[RollInput], *, started_at: datetime,
    branch_id: str | None = None,
) -> GameState:
    """Rebuild without mutating the initial setup; all nondeterministic inputs are explicit."""
    if initial.status is not GameStatus.READY or initial.turns:
        raise ValueError("再生には開始前の初期状態が必要です")
    state = deepcopy(initial)
    if branch_id is not None:
        if not branch_id.strip():
            raise ValueError("ブランチIDは空にできません")
        state.branch_id = branch_id
    state.start(now=started_at)
    for roll in rolls:
        state.roll(roll.value, now=roll.occurred_at)
    return state


def replay_operations(
    initial: GameState, operations: list[OperationInput], *, branch_id: str | None = None
) -> GameState:
    """Replay every state-changing game operation from an untouched setup."""
    if initial.status is not GameStatus.READY or initial.turns:
        raise ValueError("再生には開始前の初期状態が必要です")
    state = deepcopy(initial)
    if branch_id is not None:
        if not branch_id.strip():
            raise ValueError("ブランチIDは空にできません")
        state.branch_id = branch_id
    for operation in operations:
        record = OperationRecord(
            id=str(uuid5(
                NAMESPACE_URL,
                f"{state.id}/{state.branch_id}/event/{len(state.operation_events) + 1}",
            )),
            sequence=len(state.operation_events) + 1,
            timeline_index=len(state.operation_events) + 1,
            type=operation.type,
            payload=deepcopy(operation.payload),
            occurred_at=operation.occurred_at,
            branch_id=state.branch_id,
            state_version=state.state_version + 1,
        )
        state._apply_record(record)
        state.operation_events.append(record)
        state.undo_history.append(HistoryAction(
            id=record.id,
            label=operation.type.value,
            activated_event_ids=[record.id],
            deactivated_event_ids=[],
            branch_before=state.branch_id,
            branch_after=state.branch_id,
        ))
        state.state_version = record.state_version
    return state

