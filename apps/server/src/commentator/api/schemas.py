"""Public request models for the initial HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class RequestModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, str_strip_whitespace=True)


class PlayerCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(min_length=1, max_length=40)
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    spoken_name: str | None = Field(default=None, min_length=1, max_length=120)


class GameCreate(RequestModel):
    players: list[PlayerCreate] = Field(min_length=2, max_length=6)
    board_id: str | None = Field(default=None, alias="boardId", min_length=1, max_length=40)

    @model_validator(mode="after")
    def unique_colors(self) -> GameCreate:
        colors = [player.color.casefold() for player in self.players]
        if len(colors) != len(set(colors)):
            raise ValueError("プレイヤーの色は重複できません")
        return self


class RollCreate(RequestModel):
    value: int = Field(ge=1, le=6)
    expected_state_version: int = Field(alias="expectedStateVersion", ge=0)
    expected_turn_number: int | None = Field(default=None, alias="expectedTurnNumber", ge=0)


class RecognitionStart(RequestModel):
    camera_id: int | None = Field(default=None, alias="cameraId", ge=0)


class RecognitionCandidateConfirm(RequestModel):
    game_id: str = Field(alias="gameId", min_length=1)
    candidate_id: str = Field(alias="candidateId", min_length=1)
    expected_state_version: int = Field(alias="expectedStateVersion", ge=0)
    expected_turn_number: int = Field(alias="expectedTurnNumber", ge=0)
    value: int | None = Field(default=None, ge=1, le=6)


class RecognitionCandidateReject(RequestModel):
    candidate_id: str = Field(alias="candidateId", min_length=1)


class VersionedRequest(RequestModel):
    expected_state_version: int = Field(alias="expectedStateVersion", ge=0)
    confirm_ended_game: bool = Field(default=False, alias="confirmEndedGame")


class PositionCorrection(VersionedRequest):
    player_id: str = Field(alias="playerId", min_length=1)
    position: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    operator: str = Field(min_length=1, max_length=120)


class TurnChange(VersionedRequest):
    player_id: str = Field(alias="playerId", min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    operator: str = Field(min_length=1, max_length=120)


class RollCorrection(VersionedRequest):
    value: int = Field(ge=1, le=6)
    reason: str = Field(min_length=1, max_length=500)
    operator: str = Field(min_length=1, max_length=120)
    dry_run: bool = Field(default=False, alias="dryRun")
    confirm_invalidated_rolls: bool = Field(default=False, alias="confirmInvalidatedRolls")


class ResumeFromTurn(VersionedRequest):
    turn_id: str | None = Field(default=None, alias="turnId", min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    operator: str = Field(min_length=1, max_length=120)


class RestoreRequest(VersionedRequest):
    target_state_version: int = Field(alias="targetStateVersion", ge=0)



class BoardJumpInput(RequestModel):
    type: Literal["ladder", "snake"]
    from_: int = Field(alias="from")
    to: int


class BoardDefinition(RequestModel):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    goal: int = 100
    jumps: list[BoardJumpInput] = Field(default_factory=list, max_length=60)

    def to_board_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "jumps": [
                {"type": jump.type, "from": jump.from_, "to": jump.to} for jump in self.jumps
            ],
        }


class LoggingSettingsUpdate(RequestModel):
    retention: Literal["rotate", "permanent"]
