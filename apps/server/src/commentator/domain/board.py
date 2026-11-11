"""Board configuration and movement rules."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class JumpType(StrEnum):
    SNAKE = "snake"
    LADDER = "ladder"


@dataclass(frozen=True, slots=True)
class Jump:
    type: JumpType
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class MoveResult:
    before: int
    roll: int
    normal_position: int
    final_position: int
    overshoot: bool
    jump: Jump | None


@dataclass(frozen=True, slots=True)
class Board:
    id: str
    name: str
    goal: int
    jumps: tuple[Jump, ...]
    jump_by_start: Mapping[int, Jump] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.goal < 2:
            raise ValueError("ゴールは2以上である必要があります")

        starts: set[int] = set()
        for jump in self.jumps:
            if not 1 <= jump.start < self.goal:
                raise ValueError(f"開始マスが範囲外です: {jump.start}")
            if not 1 <= jump.end <= self.goal:
                raise ValueError(f"終了マスが範囲外です: {jump.end}")
            if jump.start in starts:
                raise ValueError(f"開始マスが重複しています: {jump.start}")
            starts.add(jump.start)
            if jump.type is JumpType.SNAKE and jump.end >= jump.start:
                raise ValueError(f"ヘビは下のマスへ移動する必要があります: {jump.start}")
            if jump.type is JumpType.LADDER and jump.end <= jump.start:
                raise ValueError(f"ハシゴは上のマスへ移動する必要があります: {jump.start}")

        chained = [jump for jump in self.jumps if jump.end in starts]
        if chained:
            first = chained[0]
            raise ValueError(
                f"ヘビ・ハシゴの移動先を別の開始マスにはできません: "
                f"{first.start} -> {first.end}"
            )

        object.__setattr__(self, "jump_by_start", MappingProxyType({
            jump.start: jump for jump in self.jumps
        }))
        # Reverse reachability also rejects reachable traps, not just a blocked start.
        reverse: dict[int, set[int]] = {square: set() for square in range(self.goal + 1)}
        for square in range(self.goal):
            for roll in range(1, 7):
                reverse[self.move(square, roll).final_position].add(square)
        reachable = {self.goal}
        pending = [self.goal]
        while pending:
            for square in reverse[pending.pop()] - reachable:
                reachable.add(square)
                pending.append(square)
        if len(reachable) != self.goal + 1:
            raise ValueError("ゴールへ到達できないマスを含む盤面です")

    def winning_rolls(self, position: int) -> list[int]:
        if position == self.goal:
            return []
        return [
            roll for roll in range(1, 7)
            if self.move(position, roll).final_position == self.goal
        ]

    def __deepcopy__(self, memo: dict[int, Any]) -> Board:
        return self

    def move(self, position: int, roll: int) -> MoveResult:
        if not 0 <= position <= self.goal:
            raise ValueError(f"現在位置が範囲外です: {position}")
        if not 1 <= roll <= 6:
            raise ValueError(f"出目は1〜6である必要があります: {roll}")
        if position == self.goal:
            raise ValueError("ゴール済みの位置からは移動できません")

        candidate = position + roll
        if candidate > self.goal:
            return MoveResult(position, roll, position, position, True, None)

        jump = self.jump_by_start.get(candidate)
        final_position = jump.end if jump else candidate
        return MoveResult(position, roll, candidate, final_position, False, jump)

    def minimum_rolls_from(self, position: int) -> int:
        """Return the theoretical minimum rolls to reach the exact goal."""

        if not 0 <= position <= self.goal:
            raise ValueError(f"現在位置が範囲外です: {position}")
        if position == self.goal:
            return 0

        queue: deque[tuple[int, int]] = deque([(position, 0)])
        visited = {position}
        while queue:
            current, distance = queue.popleft()
            for roll in range(1, 7):
                result = self.move(current, roll)
                destination = result.final_position
                if destination == self.goal:
                    return distance + 1
                if destination not in visited:
                    visited.add(destination)
                    queue.append((destination, distance + 1))
        raise RuntimeError("ゴールへ到達できない盤面です")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Board:
        jumps = tuple(
            Jump(JumpType(item["type"]), int(item["from"]), int(item["to"]))
            for item in value.get("jumps", [])
        )
        return cls(
            id=str(value["id"]),
            name=str(value["name"]),
            goal=int(value.get("goal", 100)),
            jumps=jumps,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "jumps": [
                {"type": jump.type.value, "from": jump.start, "to": jump.end}
                for jump in self.jumps
            ],
        }

