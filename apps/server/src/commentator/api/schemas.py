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


class CommentarySettingsUpdate(RequestModel):
    """Only the given fields change. API keys are never accepted here."""

    model_config = ConfigDict(
        alias_generator=to_camel, str_strip_whitespace=True, extra="forbid"
    )

    enabled: bool | None = None
    provider: Literal["fake", "claude", "openai"] | None = None
    claude_model: str | None = Field(default=None, min_length=1, max_length=100)
    openai_model: str | None = Field(default=None, min_length=1, max_length=100)
    language: Literal["ja", "en"] | None = None
    length: Literal["short", "medium", "long"] | None = None
    style: Literal["lively", "calm", "kids"] | None = None
    use_names: bool | None = None
    timeout_seconds: float | None = Field(default=None, ge=1, le=60)
    max_retries: int | None = Field(default=None, ge=0, le=3)
    # One of the prompt versions listed in the settings response.
    prompt_version: str | None = Field(default=None, min_length=1, max_length=50)


class CommentaryRegenerate(RequestModel):
    turn_id: str | None = Field(default=None, min_length=1, max_length=200)


class SpeechSettingsUpdate(RequestModel):
    """Only the given fields change; send ``null`` to use the voice's default."""

    model_config = ConfigDict(
        alias_generator=to_camel, str_strip_whitespace=True, extra="forbid"
    )

    enabled: bool | None = None
    voice: str | None = Field(default=None, max_length=100)
    rate: int | None = Field(default=None, ge=80, le=400)
    volume: float | None = Field(default=None, ge=0, le=1)
    provider: Literal["macos", "google", "gemini"] | None = None
    google_voice: str | None = Field(default=None, min_length=1, max_length=100)
    google_speaking_rate: float | None = Field(default=None, ge=0.5, le=2.0)
    gemini_backend: Literal["cloud", "api"] | None = None
    gemini_model: str | None = Field(default=None, min_length=1, max_length=100)
    gemini_voice: str | None = Field(default=None, min_length=1, max_length=50)
    gemini_style: str | None = Field(default=None, max_length=500)


class SpeechPreview(RequestModel):
    """Try a voice; settings given here are used for this preview only."""

    text: str | None = Field(default=None, max_length=300)
    voice: str | None = Field(default=None, max_length=100)
    rate: int | None = Field(default=None, ge=80, le=400)
    volume: float | None = Field(default=None, ge=0, le=1)
    provider: Literal["macos", "google", "gemini"] | None = None
    google_voice: str | None = Field(default=None, min_length=1, max_length=100)
    google_speaking_rate: float | None = Field(default=None, ge=0.5, le=2.0)
    gemini_backend: Literal["cloud", "api"] | None = None
    gemini_model: str | None = Field(default=None, min_length=1, max_length=100)
    gemini_voice: str | None = Field(default=None, min_length=1, max_length=50)
    gemini_style: str | None = Field(default=None, max_length=500)


class VisionRoiUpdate(RequestModel):
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")

    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    width: float | None = Field(default=None, gt=0, le=1)
    height: float | None = Field(default=None, gt=0, le=1)


class VisionResolutionUpdate(RequestModel):
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")

    width: int
    height: int


class VisionSettingsUpdate(RequestModel):
    """Only the given fields change.

    ``null`` means automatic for ``whiteThreshold``, ``pipThreshold`` and
    ``exposure``. Changes apply to the running recognition at once; they are
    written to the settings file only by ``POST /api/settings/vision/save``.
    """

    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")

    camera_id: int | None = Field(default=None, ge=0, le=15)
    roi: VisionRoiUpdate | None = None
    white_threshold: int | None = Field(default=None, ge=0, le=255)
    pip_threshold: int | None = Field(default=None, ge=0, le=255)
    camera_resolution: VisionResolutionUpdate | None = None
    exposure: float | None = Field(default=None, ge=-13, le=0)
    automatic_threshold: float | None = Field(default=None, ge=0.5, le=1)
    confirmation_threshold: float | None = Field(default=None, ge=0.5, le=1)
    stationary_frames: int | None = Field(default=None, ge=1, le=15)
    consecutive_value_frames: int | None = Field(default=None, ge=1, le=5)
    min_pip_size_consistency: float | None = Field(default=None, ge=0, le=1)
    max_single_pip_center_offset: float | None = Field(default=None, ge=0, le=0.5)
    max_pip_layout_error: float | None = Field(default=None, ge=0, le=1)
    min_detector_confidence: float | None = Field(default=None, ge=0, le=1)
    tray_detection: bool | None = None


class VisionSettingsRevert(RequestModel):
    target: Literal["saved", "defaults"] = "saved"


class VlmSettingsUpdate(RequestModel):
    """Only the given fields change. The API key is never accepted here."""

    model_config = ConfigDict(
        alias_generator=to_camel, str_strip_whitespace=True, extra="forbid"
    )

    reading_mode: Literal["opencv", "vlm", "hybrid"] | None = None
    model: str | None = Field(default=None, min_length=1, max_length=100)
    timeout_seconds: float | None = Field(default=None, ge=2, le=30)
    max_retries: int | None = Field(default=None, ge=0, le=2)
    save_images: bool | None = None
