"""The facts sent by prompt commentary-v8: v7's, with like chances grouped (2026-11-23).

Next to each other, the 48 and 49 snakes made four separate swings for one
player (dodged 48, dodged 49, next 48, next 49). The model mentions at most two
swings, so it always told the bigger snake and never the other. v8 puts the
same kind of swing for the same player into one item: the biggest stays in
front and the others are listed in ``alsoWith``, so "a 2 or a 3 means a snake"
is one topic.
"""

from __future__ import annotations

from typing import Any

from .outlook import MAX_SWINGS, NamePlayer
from .outlook_v7 import analyze_turn as analyze_v7

GROUPED = {"missed_ladder", "avoided_snake", "next_ladder", "next_snake"}
_OTHER_KEYS = ("roll", "from", "to", "squares", "direction", "byPips")


def group_swings(swings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the order; fold later swings of the same kind and player into the first."""
    grouped: list[dict[str, Any]] = []
    first: dict[tuple[str, str], dict[str, Any]] = {}
    for swing in swings:
        key = (swing["kind"], swing["player"])
        if swing["kind"] in GROUPED and key in first:
            other = {name: swing[name] for name in _OTHER_KEYS if name in swing}
            first[key].setdefault("alsoWith", []).append(other)
            continue
        item = dict(swing)
        first[key] = item
        grouped.append(item)
    return grouped


def analyze_turn(
    game: dict[str, Any], turn_index: int, name: NamePlayer
) -> dict[str, Any]:
    facts = analyze_v7(game, turn_index, name, trim=False)
    facts["biggestSwings"] = group_swings(facts["biggestSwings"])[:MAX_SWINGS]
    return facts
