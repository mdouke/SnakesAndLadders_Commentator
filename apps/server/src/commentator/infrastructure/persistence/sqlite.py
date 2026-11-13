"""Atomic SQLite persistence for game state and its append-only history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from ...domain.board import Board, JumpType
from ...domain.game import (
    EventType,
    GameState,
    GameStatus,
    HistoryAction,
    OperationRecord,
    OperationType,
    PlayerState,
    TurnRecord,
    compute_statistics,
)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _player_data(player: PlayerState) -> dict[str, Any]:
    return {
        "id": player.id,
        "name": player.name,
        "displayName": player.display_name,
        "spokenName": player.spoken_name,
        "color": player.color,
        "position": player.position,
        "rank": player.rank,
        "rankConfirmed": player.rank_confirmed,
        "reachedGoal": player.reached_goal,
        "reachedGoalAt": player.reached_goal_at.isoformat() if player.reached_goal_at else None,
        "statistics": player.statistics.to_dict(),
    }


def _player(value: dict[str, Any]) -> PlayerState:
    return PlayerState(
        id=str(value["id"]),
        name=str(value["name"]),
        display_name=str(value["displayName"]),
        spoken_name=str(value["spokenName"]),
        color=str(value["color"]),
        position=int(value["position"]),
        rank=int(value["rank"]) if value.get("rank") is not None else None,
        rank_confirmed=bool(value["rankConfirmed"]),
        reached_goal=bool(value["reachedGoal"]),
        reached_goal_at=_dt(value.get("reachedGoalAt")),
    )


def _turn_data(turn: TurnRecord) -> dict[str, Any]:
    return turn.to_dict()


def _turn(value: dict[str, Any]) -> TurnRecord:
    jump = value.get("jumpType")
    return TurnRecord(
        id=str(value["id"]),
        number=int(value["number"]),
        player_id=str(value["playerId"]),
        roll=int(value["roll"]),
        before=int(value["before"]),
        normal_position=int(value["normalPosition"]),
        final_position=int(value["finalPosition"]),
        overshoot=bool(value["overshoot"]),
        jump_type=JumpType(jump) if jump else None,
        events=tuple(EventType(item) for item in value["events"]),
        occurred_at=datetime.fromisoformat(value["occurredAt"]),
        branch_id=str(value.get("branchId", "main")),
        state_version_before=int(value.get("stateVersionBefore", 0)),
        state_version_after=int(value.get("stateVersionAfter", 0)),
    )


def game_to_snapshot(game: GameState) -> dict[str, Any]:
    return {
        "id": game.id,
        "board": game.board.to_dict(),
        "players": [_player_data(item) for item in game.players],
        "initialPlayers": [_player_data(item) for item in game.initial_players or []],
        "branchId": game.branch_id,
        "status": game.status.value,
        "currentPlayerIndex": game.current_player_index,
        "turnNumber": game.turn_number,
        "turns": [_turn_data(item) for item in game.turns],
        "startedAt": game.started_at.isoformat() if game.started_at else None,
        "endedAt": game.ended_at.isoformat() if game.ended_at else None,
        "suspendedAt": game.suspended_at.isoformat() if game.suspended_at else None,
        "suspensionHistory": [item.isoformat() for item in game.suspension_history],
        "stateVersion": game.state_version,
        "undoHistory": [item.to_dict() for item in game.undo_history],
        "redoHistory": [item.to_dict() for item in game.redo_history],
        "interruptionDetectedAt": (
            game.interruption_detected_at.isoformat()
            if game.interruption_detected_at else None
        ),
        "interruptionLastSavedAt": (
            game.interruption_last_saved_at.isoformat()
            if game.interruption_last_saved_at else None
        ),
        "operationEvents": [item.to_dict() for item in game.operation_events],
    }


def _history_action(value: dict[str, Any]) -> HistoryAction:
    return HistoryAction(
        id=str(value["id"]),
        label=str(value["label"]),
        activated_event_ids=[str(item) for item in value["activatedEventIds"]],
        deactivated_event_ids=[str(item) for item in value["deactivatedEventIds"]],
        branch_before=str(value["branchBefore"]),
        branch_after=str(value["branchAfter"]),
    )


def game_from_snapshot(value: dict[str, Any]) -> GameState:
    events = [
        OperationRecord(
            id=str(item["id"]),
            sequence=int(item["sequence"]),
            timeline_index=(
                int(item["timelineIndex"]) if item.get("timelineIndex") is not None else None
            ),
            type=OperationType(item["type"]),
            payload=dict(item["payload"]),
            occurred_at=datetime.fromisoformat(item["occurredAt"]),
            branch_id=str(item["branchId"]),
            state_version=int(item["stateVersion"]),
            active=bool(item["active"]),
        )
        for item in value["operationEvents"]
    ]
    game = GameState(
        id=str(value["id"]),
        board=Board.from_dict(value["board"]),
        players=[_player(item) for item in value["players"]],
        initial_players=[_player(item) for item in value["initialPlayers"]],
        branch_id=str(value["branchId"]),
        suspension_history=[datetime.fromisoformat(item) for item in value["suspensionHistory"]],
        status=GameStatus(value["status"]),
        current_player_index=value["currentPlayerIndex"],
        turn_number=int(value["turnNumber"]),
        turns=[_turn(item) for item in value["turns"]],
        started_at=_dt(value.get("startedAt")),
        ended_at=_dt(value.get("endedAt")),
        suspended_at=_dt(value.get("suspendedAt")),
        state_version=int(value["stateVersion"]),
        operation_events=events,
        undo_history=[_history_action(item) for item in value.get("undoHistory", [])],
        redo_history=[_history_action(item) for item in value.get("redoHistory", [])],
        interruption_detected_at=_dt(value.get("interruptionDetectedAt")),
        interruption_last_saved_at=_dt(value.get("interruptionLastSavedAt")),
    )
    for player in game.players:
        player.statistics = compute_statistics([
            turn for turn in game.turns if turn.player_id == player.id
        ])
    return game


class SQLiteGameRepository:
    """Stores the aggregate, events, turns and snapshots in one transaction."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.isolation_level = "IMMEDIATE"
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS games (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operation_events (
                    id TEXT PRIMARY KEY,
                    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    timeline_index INTEGER,
                    type TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    active INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_events_game_sequence
                    ON operation_events(game_id, sequence);
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL,
                    branch_id TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_turns_game_number ON turns(game_id, number);
                CREATE TABLE IF NOT EXISTS snapshots (
                    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    state_version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (game_id, state_version)
                );
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            connection.execute(
                """
                INSERT INTO app_state(key, value)
                SELECT 'current_game_id', id FROM games
                WHERE NOT EXISTS (
                    SELECT 1 FROM app_state WHERE key = 'current_game_id'
                )
                ORDER BY updated_at DESC LIMIT 1
                """
            )
            connection.commit()

    def get(self, game_id: str) -> GameState | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM games WHERE id = ?", (game_id,)
            ).fetchone()
        return game_from_snapshot(json.loads(row["snapshot_json"])) if row else None

    def save(self, game: GameState) -> None:
        self._save(game, make_current=False)

    def save_and_set_current(self, game: GameState) -> None:
        self._save(game, make_current=True)

    def _save(self, game: GameState, *, make_current: bool) -> None:
        snapshot = json.dumps(game_to_snapshot(game), ensure_ascii=False, separators=(",", ":"))
        updated_at = datetime.now().astimezone().isoformat()
        with self._lock, closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO games(id, status, state_version, snapshot_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                        state_version=excluded.state_version,
                        snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at
                    """,
                    (game.id, game.status.value, game.state_version, snapshot, updated_at),
                )
                for event in game.operation_events:
                    connection.execute(
                        """
                        INSERT INTO operation_events(
                            id, game_id, sequence, timeline_index, type, branch_id,
                            state_version, active, payload_json, occurred_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET active=excluded.active,
                            payload_json=excluded.payload_json
                        """,
                        (
                            event.id, game.id, event.sequence, event.timeline_index,
                            event.type.value, event.branch_id, event.state_version,
                            int(event.active), json.dumps(event.payload, ensure_ascii=False),
                            event.occurred_at.isoformat(),
                        ),
                    )
                connection.execute("UPDATE turns SET active = 0 WHERE game_id = ?", (game.id,))
                for turn in game.turns:
                    connection.execute(
                        """
                        INSERT INTO turns(id, game_id, number, branch_id, active, data_json)
                        VALUES (?, ?, ?, ?, 1, ?)
                        ON CONFLICT(id) DO UPDATE SET active=1, data_json=excluded.data_json
                        """,
                        (
                            turn.id, game.id, turn.number, turn.branch_id,
                            json.dumps(_turn_data(turn), ensure_ascii=False),
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO snapshots(game_id, state_version, snapshot_json, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(game_id, state_version) DO UPDATE SET
                        snapshot_json=excluded.snapshot_json, created_at=excluded.created_at
                    """,
                    (game.id, game.state_version, snapshot, updated_at),
                )
                if make_current:
                    connection.execute(
                        """
                        INSERT INTO app_state(key, value) VALUES ('current_game_id', ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value
                        """,
                        (game.id,),
                    )
    def current(self) -> GameState | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = 'current_game_id'"
            ).fetchone()
        return self.get(str(row["value"])) if row and row["value"] else None

    def set_current(self, game_id: str) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            exists = connection.execute(
                "SELECT 1 FROM games WHERE id = ?", (game_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("ゲームが見つかりません")
            connection.execute(
                """
                INSERT INTO app_state(key, value) VALUES ('current_game_id', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (game_id,),
            )

    def last_saved_at(self, game_id: str) -> datetime | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT updated_at FROM games WHERE id = ?", (game_id,)
            ).fetchone()
        return datetime.fromisoformat(str(row["updated_at"])) if row else None

    def list(self) -> list[GameState]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT snapshot_json FROM games ORDER BY updated_at DESC"
            ).fetchall()
        return [game_from_snapshot(json.loads(row["snapshot_json"])) for row in rows]

    def snapshot(self, game_id: str, state_version: int) -> GameState | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM snapshots WHERE game_id = ? AND state_version = ?",
                (game_id, state_version),
            ).fetchone()
        return game_from_snapshot(json.loads(row["snapshot_json"])) if row else None
