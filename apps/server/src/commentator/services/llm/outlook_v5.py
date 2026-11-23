"""The facts sent by prompt commentary-v5: v4's, with one change (2026-11-23).

A roll that actually climbed a ladder, slid down a snake or reached the goal
keeps the feeling of what happened. In v4 a bigger snake dodged on the way
("a 4 would have dropped you to 11") outranked the ladder that was climbed, so
the comment opened with "Phew!" for a lucky roll.
"""

from __future__ import annotations

from typing import Any

from .outlook import NamePlayer
from .outlook_v4 import analyze_turn as analyze_v4


def analyze_turn(
    game: dict[str, Any], turn_index: int, name: NamePlayer, *, trim: bool = True
) -> dict[str, Any]:
    return analyze_v4(game, turn_index, name, keep_actual_jump=True, trim=trim)
