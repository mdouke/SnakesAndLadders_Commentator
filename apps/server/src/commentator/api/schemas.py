"""Public request models for the initial HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class RequestModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, str_strip_whitespace=True)


class PlayerCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(min_length=1, max_length=40)
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    spoken_name: str | None = Field(default=None, min_length=1, max_length=120)


class GameCreate(RequestModel):
    players: list[PlayerCreate] = Field(min_length=2)


class RollCreate(RequestModel):
    value: int = Field(ge=1, le=6)
    expected_turn_number: int = Field(alias="expectedTurnNumber", ge=0)

