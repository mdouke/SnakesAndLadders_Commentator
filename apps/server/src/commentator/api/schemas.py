"""Public request models for the initial HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlayerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(min_length=1, max_length=40)
    display_name: str | None = Field(default=None, max_length=80)
    spoken_name: str | None = Field(default=None, max_length=120)


class GameCreate(BaseModel):
    players: list[PlayerCreate] = Field(min_length=2)


class RollCreate(BaseModel):
    value: int = Field(ge=1, le=6)

