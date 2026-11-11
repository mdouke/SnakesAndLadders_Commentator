"""Deterministic game state and turn processing."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from .board import Board, Jump, JumpType


class GameStatus(StrEnum):
    READY = "ready"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    ABORTED = "aborted"


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

    def to_dict(self, *, is_current: bool, goal: int, minimum_rolls: int) -> dict[str, Any]:
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
            "requiredRoll": goal - self.position if 1 <= goal - self.position <= 6 else None,
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
    events: tuple[str, ...]
    occurred_at: datetime

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
        }


@dataclass(slots=True)
class GameState:
    id: str
    board: Board
    players: list[PlayerState]
    status: GameStatus = GameStatus.READY
    current_player_index: int | None = 0
    turn_number: int = 0
    turns: list[TurnRecord] = field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    suspended_at: datetime | None = None

    def __post_init__(self) -> None:
        if len(self.players) < 2:
            raise ValueError("プレイヤーは2人以上必要です")
        ids = [player.id for player in self.players]
        if len(ids) != len(set(ids)):
            raise ValueError("プレイヤーIDが重複しています")
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
        if self.status is not GameStatus.READY:
            raise ValueError("準備中のゲームだけを開始できます")
        self.status = GameStatus.IN_PROGRESS
        self.started_at = now or datetime.now(UTC)

    def roll(self, value: int, *, now: datetime | None = None) -> TurnRecord:
        if self.status is not GameStatus.IN_PROGRESS:
            raise ValueError("進行中のゲームでのみサイコロを確定できます")
        player = self.current_player
        if player is None or player.reached_goal:
            raise RuntimeError("現在の手番が不正です")

        occurred_at = now or datetime.now(UTC)
        result = self.board.move(player.position, value)
        self.turn_number += 1
        events = self._events_for(result.overshoot, result.jump, result.final_position, value)
        turn = TurnRecord(
            id=str(uuid4()),
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
        )
        self.turns.append(turn)
        self._apply_statistics(player, turn, result.jump)
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

    def pause(self) -> None:
        if self.status is not GameStatus.IN_PROGRESS:
            raise ValueError("進行中のゲームだけを一時停止できます")
        self.status = GameStatus.PAUSED

    def resume(self) -> None:
        if self.status not in {GameStatus.PAUSED, GameStatus.SUSPENDED}:
            raise ValueError("一時停止または保存終了したゲームだけを再開できます")
        self.status = GameStatus.IN_PROGRESS
        self.suspended_at = None

    def suspend(self, *, now: datetime | None = None) -> None:
        if self.status not in {GameStatus.IN_PROGRESS, GameStatus.PAUSED}:
            raise ValueError("進行中または一時停止中のゲームだけを保存終了できます")
        self.status = GameStatus.SUSPENDED
        self.suspended_at = now or datetime.now(UTC)

    def abort(self, *, now: datetime | None = None) -> None:
        if self.status in {GameStatus.COMPLETED, GameStatus.ABORTED}:
            raise ValueError("終了済みのゲームは中止できません")
        self.status = GameStatus.ABORTED
        self.ended_at = now or datetime.now(UTC)
        self.current_player_index = None
        for player in self.players:
            player.rank_confirmed = False

    def rankings(self) -> dict[str, int]:
        result = {
            player.id: player.rank
            for player in self.players
            if player.reached_goal and player.rank is not None
        }
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

    def to_dict(self) -> dict[str, Any]:
        current_id = self.current_player.id if self.current_player else None
        return {
            "id": self.id,
            "boardId": self.board.id,
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
                )
                for player in self.players
            ],
            "turns": [turn.to_dict() for turn in self.turns],
        }

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

    def _apply_statistics(
        self, player: PlayerState, turn: TurnRecord, jump: Jump | None
    ) -> None:
        stats = player.statistics
        stats.rolls.append(turn.roll)
        stats.face_counts[turn.roll] += 1
        stats.normal_movement_total += 0 if turn.overshoot else turn.roll
        stats.actual_position_change_total += abs(turn.final_position - turn.before)
        stats.overshoot_count += int(turn.overshoot)
        stats.maximum_position = max(stats.maximum_position, turn.final_position)
        if jump is None:
            return
        history = JumpHistory(turn.number, jump.start, jump.end)
        if jump.type is JumpType.LADDER:
            stats.ladder_count += 1
            stats.ladder_distance_total += jump.end - jump.start
            stats.ladder_history.append(history)
        else:
            stats.snake_count += 1
            stats.snake_distance_total += jump.start - jump.end
            stats.snake_history.append(history)

    def _events_for(
        self,
        overshoot: bool,
        jump: Jump | None,
        final_position: int,
        value: int,
    ) -> tuple[str, ...]:
        events = ["normal_move"]
        if value >= 5:
            events.append("large_roll")
        elif value <= 2:
            events.append("small_roll")
        if overshoot:
            events = ["goal_overshoot"]
        elif jump is not None:
            events.append("ladder" if jump.type is JumpType.LADDER else "snake")
        if final_position == self.board.goal:
            events.append("goal")
        elif self.board.goal - final_position <= 6:
            events.append("near_goal")
        return tuple(events)

