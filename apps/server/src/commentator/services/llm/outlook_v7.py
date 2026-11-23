"""The facts sent by prompt commentary-v7: v5's, plus which way a near miss went (2026-11-23).

A ladder or the goal missed this roll was either passed (the roll was bigger
than the one needed: "went past it") or not reached (smaller: "fell short").
v6 said "fell short" for a 5 that went past the ladder a 2 would have hit.
``direction`` and ``byPips`` tell the two apart; the same goes for a snake
dodged.
"""

from __future__ import annotations

from typing import Any

from .outlook import NamePlayer
from .outlook_v5 import analyze_turn as analyze_v5

NEAR_MISSES = {"missed_goal", "missed_ladder", "avoided_snake"}


def _direction(needed: int, rolled: int) -> dict[str, Any]:
    return {
        "direction": "passed" if rolled > needed else "short",
        "byPips": abs(rolled - needed),
    }


def analyze_turn(
    game: dict[str, Any], turn_index: int, name: NamePlayer
) -> dict[str, Any]:
    facts = analyze_v5(game, turn_index, name)
    rolled = int(game["turns"][turn_index]["roll"])
    for swing in facts["biggestSwings"]:
        if swing["kind"] in NEAR_MISSES:
            swing.update(_direction(int(swing["roll"]), rolled))
    emotion = facts["emotion"]
    if emotion.get("about") in NEAR_MISSES:
        emotion.update(_direction(int(emotion["roll"]), rolled))
    return facts
