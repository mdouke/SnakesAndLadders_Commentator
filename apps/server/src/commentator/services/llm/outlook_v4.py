"""The facts added by prompt commentary-v4, on top of v3's (``outlook.py``).

From a review of a full game with v3:

- ``emotion`` replaces ``reaction``. It also comes from what this roll could
  have done: a goal missed by one pip is "so close", a decisive snake dodged is
  "relief". v3 reacted only to what actually happened, so near misses were told
  as plain facts.
- ``race.phase`` is "opening" until every player has rolled twice; v3 called
  the very first roll a close race.
- A ladder or snake the mover could have hit this roll and can still hit next
  roll is marked ``stillInReach`` ("the chance goes on").
- ``previousComments`` (added by the context) are the last comments said, so
  the model can avoid repeating itself and follow up what it announced.
"""

from __future__ import annotations

from typing import Any

from .outlook import MAX_SWINGS, NamePlayer
from .outlook import analyze_turn as analyze_v3

OPENING_ROLLS_PER_PLAYER = 2

# (kind, strength) of emotions, strongest first within a kind.
STRENGTHS = ("huge", "big", "mild")
_REACTION_TO_EMOTION = {
    "huge_cheer": ("joy", "huge"), "cheer": ("joy", "big"), "mild_cheer": ("joy", "mild"),
    "huge_groan": ("disappointment", "huge"), "groan": ("disappointment", "big"),
    "mild_groan": ("disappointment", "mild"), "calm": ("calm", None),
}
_IMPACT_TO_STRENGTH = {"decisive": "huge", "big": "big"}


def _size_strength(squares: int | None, goal: int) -> str | None:
    if squares is None:
        return None
    if squares / goal >= 0.30:
        return "huge"
    if squares / goal >= 0.15:
        return "big"
    return None


def _stronger(first: str | None, second: str | None) -> str | None:
    ranked = [value for value in (first, second) if value is not None]
    return min(ranked, key=STRENGTHS.index) if ranked else None


def _emotion(facts: dict[str, Any], swings: list[dict[str, Any]], goal: int) -> dict[str, Any]:
    """The feeling of this roll: what happened, or what nearly happened."""
    kind, strength = _REACTION_TO_EMOTION[facts["reaction"]]
    best: dict[str, Any] = {"kind": kind, "strength": strength, "about": "actual"}
    for swing in swings:
        if swing["kind"] not in {"missed_goal", "missed_ladder", "avoided_snake"}:
            continue
        near = _stronger(_IMPACT_TO_STRENGTH.get(swing["impact"]),
                         _size_strength(swing["squares"], goal))
        if swing["kind"] == "missed_goal":
            near = _stronger(near, "big")  # one pip from the goal is always a moment
        if near not in {"huge", "big"}:
            continue  # small near misses happen every roll; they are not a moment
        current = best["strength"]
        if current is None or STRENGTHS.index(near) < STRENGTHS.index(current):
            best = {
                "kind": "relief" if swing["kind"] == "avoided_snake" else "so_close",
                "strength": near,
                "about": swing["kind"],
                "roll": swing["roll"],
                "to": swing["to"],
            }
    return best


def analyze_turn(
    game: dict[str, Any], turn_index: int, name: NamePlayer
) -> dict[str, Any]:
    facts = analyze_v3(game, turn_index, name, max_swings=None)
    goal = int(game["board"]["goal"])
    swings: list[dict[str, Any]] = facts["biggestSwings"]

    # A jump the mover could have hit this roll and can still hit next roll.
    this_roll = {
        (item["player"], item["from"]) for item in swings
        if item["kind"] in {"missed_ladder", "avoided_snake"}
    }
    for item in swings:
        if item["kind"] in {"next_ladder", "next_snake"}:
            item["stillInReach"] = (item["player"], item["from"]) in this_roll

    emotion = _emotion(facts, swings, goal)
    rolls = len(game["turns"][: turn_index + 1])
    race = facts["race"]
    if rolls < OPENING_ROLLS_PER_PLAYER * len(game["players"]):
        race["phase"] = "opening"
        race["phaseBefore"] = "opening"

    result = {key: value for key, value in facts.items() if key != "reaction"}
    result["emotion"] = emotion
    result["biggestSwings"] = swings[:MAX_SWINGS]
    return result
