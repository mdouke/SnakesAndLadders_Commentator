"""Public response models. They define the OpenAPI schema used to generate web types.

Domain objects still serialize themselves with ``to_dict()``; FastAPI validates that
output against these models, so a key added on one side but not the other fails the
API tests (``extra="forbid"``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter
from pydantic.alias_generators import to_camel


class ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )


GameStatusValue = Literal[
    "ready", "in_progress", "paused", "suspended", "completed", "aborted"
]
RollSourceValue = Literal["manual", "automatic", "reviewed", "corrected"]
RecognitionStateValue = Literal[
    "unavailable",
    "waiting",
    "moving",
    "stationary",
    "candidate",
    "confirmed",
    "needs_confirmation",
    "removal_wait",
    "recognition_error",
    "camera_error",
]


class HealthResponse(ResponseModel):
    status: Literal["ok"]


class BoardJump(ResponseModel):
    type: Literal["ladder", "snake"]
    from_: int = Field(alias="from")
    to: int


class BoardResponse(ResponseModel):
    id: str
    name: str
    goal: int
    jumps: list[BoardJump]


class PlayerStatistics(ResponseModel):
    rolls: list[int]
    face_counts: dict[str, int]
    average_roll: float
    normal_movement_total: int
    actual_position_change_total: int
    overshoot_count: int
    ladder_count: int
    ladder_distance_total: int
    snake_count: int
    snake_distance_total: int
    maximum_position: int


class PlayerResponse(ResponseModel):
    id: str
    name: str
    display_name: str
    spoken_name: str
    color: str
    position: int
    rank: int | None
    rank_confirmed: bool
    reached_goal: bool
    reached_goal_at: str | None
    remaining_squares: int
    winning_rolls: list[int]
    is_current: bool
    theoretical_minimum_rolls: int
    statistics: PlayerStatistics


class RecognitionConfidence(ResponseModel):
    detector: float
    value_stability: float
    stillness: float
    position_stability: float
    total: float
    pip_size_consistency: float
    single_pip_center_offset: float
    pip_merge_suspected: bool
    pip_recounted: bool
    pip_layout_error: float


class CorrectedFrom(ResponseModel):
    value: int
    source: RollSourceValue
    confirmation_id: str | None


class TurnResponse(ResponseModel):
    id: str
    number: int
    player_id: str
    roll: int
    before: int
    normal_position: int
    final_position: int
    overshoot: bool
    jump_type: Literal["ladder", "snake"] | None
    events: list[str]
    occurred_at: str
    source: RollSourceValue
    confirmation_id: str | None
    original_candidate: int | None
    confidence: RecognitionConfidence | None
    corrected_from: CorrectedFrom | None
    branch_id: str
    state_version_before: int
    state_version_after: int


class Interruption(ResponseModel):
    detected_at: str
    last_saved_at: str | None


class GameResponse(ResponseModel):
    id: str
    board_id: str
    branch_id: str
    state_version: int
    can_undo: bool
    can_redo: bool
    undo_label: str | None
    redo_label: str | None
    interruption: Interruption | None
    suspension_history: list[str]
    status: GameStatusValue
    turn_number: int
    current_player_id: str | None
    started_at: str | None
    ended_at: str | None
    suspended_at: str | None
    players: list[PlayerResponse]
    turns: list[TurnResponse]


InvalidationReason = Literal["player_already_finished", "game_completed"]


class InvalidatedTurn(ResponseModel):
    event_id: str
    turn_id: str | None
    turn_number: int | None
    player_id: str
    value: int
    reason: InvalidationReason


class InvalidatedOperation(ResponseModel):
    event_id: str
    timeline_index: int | None
    type: str
    reason: InvalidationReason
    player_id: str | None = None
    position: int | None = None


class RollCorrectionPreview(ResponseModel):
    requires_confirmation: bool
    invalidated_turns: list[InvalidatedTurn]
    invalidated_operations: list[InvalidatedOperation]


class RecognitionConfirmation(ResponseModel):
    confirmation_id: str
    value: int
    source: RollSourceValue
    original_candidate: int | None
    confidence: RecognitionConfidence | None


class RecognitionResponse(ResponseModel):
    available: bool
    running: bool
    camera_connected: bool
    state: RecognitionStateValue
    candidate_value: int | None
    candidate_id: str | None
    confidence: RecognitionConfidence | None
    confirmation: RecognitionConfirmation | None
    last_confirmation: RecognitionConfirmation | None
    error_code: str | None


class RecognitionCamera(ResponseModel):
    id: int
    label: str


class RecognitionCamerasResponse(ResponseModel):
    cameras: list[RecognitionCamera]


class CommentaryResponse(ResponseModel):
    """Placeholder until Phase 6 adds generated commentary."""

    status: Literal["idle"]


class GameMessage(ResponseModel):
    type: Literal["game"]
    sequence: int
    payload: GameResponse | None


class RecognitionMessage(ResponseModel):
    type: Literal["recognition"]
    sequence: int
    payload: RecognitionResponse


class CommentaryMessage(ResponseModel):
    type: Literal["commentary"]
    sequence: int
    payload: CommentaryResponse


class RealtimeMessage(
    RootModel[
        Annotated[
            GameMessage | RecognitionMessage | CommentaryMessage,
            Field(discriminator="type"),
        ]
    ]
):
    """One message on the ``/ws`` stream, selected by ``type``."""


REALTIME_MESSAGE_ADAPTER: TypeAdapter[RealtimeMessage] = TypeAdapter(RealtimeMessage)
