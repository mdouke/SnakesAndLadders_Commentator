"""What could have happened and what may happen next, computed from the board.

The commentary model is told to use only given facts, so everything it may say
about possibilities is computed here with the game's own movement rule
(``Board.move``): the other rolls of this turn, the next roll of the player who
just moved and of the player who moves next, the race between players, and how
the game has gone so far.

How much a swing matters is measured by how much it changes a player's chance
of reaching the goal first (``winning.py``), not by squares alone: a 20-square
snake next to the goal can decide the game, while a long ladder far behind may
change little. ``biggestSwings`` lists swings by that impact, largest first, so
the comment can lead with the one that matters most without the model having
to weigh numbers. The chances themselves are given as words and numbers for
judgement only; the prompt forbids saying the numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...domain.board import Board
from .winning import win_chances

MAX_SWINGS = 5

# Kinds of swings, in the order preferred when two have the same impact: what
# actually happened, then what this roll could have done, then the next rolls.
SWING_KINDS = (
    "reached_goal", "hit_ladder", "hit_snake",
    "missed_goal", "missed_ladder", "avoided_snake",
    "next_goal", "next_ladder", "next_snake",
)

# The favourite's chance must move at least this much for the phase to change.
PHASE_CHANGE_MIN = 0.03

# Change in the chance of finishing first (0..1) -> how much a swing matters.
IMPACT_LEVELS = ((0.25, "decisive"), (0.12, "big"), (0.05, "notable"))
IMPACT_ORDER = ("decisive", "big", "notable", "small")

NamePlayer = Callable[[dict[str, Any]], str]


def impact_level(change: float) -> str:
    for threshold, label in IMPACT_LEVELS:
        if abs(change) >= threshold:
            return label
    return "small"


REACTIONS = ("huge_groan", "groan", "mild_groan", "calm", "mild_cheer", "cheer", "huge_cheer")


def reaction_level(
    change: float, jump: str | None, reached_goal: bool, size: float = 0.0
) -> str:
    """How loudly to react to what actually happened to the player who rolled.

    The stronger of two measures: the change in the chance of finishing first,
    and the size of the ladder or snake (``size``: squares moved / goal square).
    A long ladder early in the game changes the chances little but is still a
    big moment at the table.
    """
    if reached_goal:
        return "huge_cheer"
    by_chance = (
        3 if change >= 0.20 else 2 if change >= 0.08 else
        -3 if change <= -0.20 else -2 if change <= -0.08 else 0
    )
    by_size = 0
    if jump is not None:
        strength = 3 if size >= 0.30 else 2 if size >= 0.15 else 1
        by_size = strength if jump == "ladder" else -strength
    level = by_chance if abs(by_chance) >= abs(by_size) else by_size
    return REACTIONS[level + 3]


def phase_of(top_chance: float) -> str:
    """How settled the race is, from the favourite's chance of finishing first."""
    if top_chance >= 0.80:
        return "nearly_decided"
    if top_chance >= 0.65:
        return "pulling_away"
    return "close"


def chance_word(chance: float) -> str:
    if chance >= 0.85:
        return "very_likely"
    if chance >= 0.60:
        return "likely"
    if chance > 0.40:
        return "even"
    if chance > 0.15:
        return "unlikely"
    return "very_unlikely"


class _Race:
    """Win chances for positions on one board with a fixed seating order."""

    def __init__(self, board: Board, seating: list[str], finished: set[str]) -> None:
        self.board = board
        self.seating = seating  # player ids in turn order
        self.finished = finished  # reached the goal earlier; no longer racing

    def following(self, player_id: str) -> str:
        """Who rolls after ``player_id`` (skipping finished players)."""
        start = self.seating.index(player_id)
        for step in range(1, len(self.seating) + 1):
            candidate = self.seating[(start + step) % len(self.seating)]
            if candidate not in self.finished:
                return candidate
        return player_id

    def chances(self, positions: dict[str, int], next_id: str) -> dict[str, float]:
        start = self.seating.index(next_id)
        order = [
            (player_id, positions[player_id])
            for player_id in self.seating[start:] + self.seating[:start]
            if player_id not in self.finished
        ]
        return win_chances(self.board, order)


def _jump_item(
    board: Board, position: int, roll: int
) -> tuple[int, int, str | None, bool]:
    result = board.move(position, roll)
    jump = result.jump.type.value if result.jump else None
    return result.normal_position, result.final_position, jump, result.overshoot


def _next_roll(
    race: _Race, name: str, player_id: str, positions: dict[str, int], now: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Ladders, snakes and goal within one roll, with each one's impact.

    The impact compares the player's chance after that roll (the next player to
    roll then being the one after them) with their chance now.
    """
    board = race.board
    position = positions[player_id]
    next_id = race.following(player_id)
    ladders: list[dict[str, Any]] = []
    snakes: list[dict[str, Any]] = []
    goal_rolls: list[int] = []
    swings: list[dict[str, Any]] = []
    for roll in range(1, 7):
        lands_on, final, jump, _ = _jump_item(board, position, roll)
        if final == board.goal:
            goal_rolls.append(roll)
        elif jump is None:
            continue
        moved = {**positions, player_id: final}
        change = race.chances(moved, next_id)[player_id] - now
        if final == board.goal:
            kind = "next_goal"
            lands_on = position  # a goal roll: from where the roll starts
        else:
            item = {"roll": roll, "from": lands_on, "to": final,
                    "squares": abs(final - lands_on)}
            (ladders if jump == "ladder" else snakes).append(item)
            kind = "next_ladder" if jump == "ladder" else "next_snake"
        swings.append(_swing(kind, name, roll, lands_on, final, jump, change))
    outlook = {
        "player": name,
        "position": position,
        "ladderChances": ladders,
        "snakeRisks": snakes,
        "goalRolls": goal_rolls,
    }
    return outlook, swings


def _swing(
    kind: str, player: str, roll: int, lands_on: int, final: int, jump: str | None,
    change: float,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "player": player,
        "roll": roll,
        "from": lands_on,
        "to": final,
        "squares": abs(final - lands_on) if jump else None,
        "impact": impact_level(change),
        "_change": change,
    }


def analyze_turn(
    game: dict[str, Any], turn_index: int, name: NamePlayer
) -> dict[str, Any]:
    """Facts about possibilities and the race for ``game["turns"][turn_index]``.

    ``name`` gives the name the prompt uses for a player payload.
    """
    board = Board.from_dict(game["board"])
    players = {player["id"]: player for player in game["players"]}
    seating = [player["id"] for player in game["players"]]
    turns = game["turns"]
    turn = turns[turn_index]
    mover = players[turn["playerId"]]
    mover_id = mover["id"]
    mover_name = name(mover)
    newest = turn_index == len(turns) - 1

    # Positions just after this roll; the newest turn uses the real positions
    # (they include position corrections).
    after = (
        {player_id: int(player["position"]) for player_id, player in players.items()}
        if newest else _positions_after(turns[: turn_index + 1], seating)
    )
    before = {**after, mover_id: int(turn["before"])}
    finished = {
        player_id for player_id, square in before.items()
        if square >= board.goal and player_id != mover_id
    }
    race_model = _Race(board, seating, finished)
    following = race_model.following(mover_id)
    chances_before = race_model.chances(before, mover_id)
    chances_after = race_model.chances(after, following)
    actual_change = chances_after[mover_id] - chances_before[mover_id]
    actual_final = int(turn["finalPosition"])

    # -- this roll: the actual result and the other rolls that mattered -----------
    swings: list[dict[str, Any]] = []
    for roll in range(1, 7):
        lands_on, final, jump, _ = _jump_item(board, int(turn["before"]), roll)
        actual = roll == turn["roll"]
        if jump is None and final != board.goal:
            continue
        if actual:
            kind = ("reached_goal" if final == board.goal
                    else "hit_ladder" if jump == "ladder" else "hit_snake")
            change = actual_change
        else:
            kind = ("missed_goal" if final == board.goal
                    else "missed_ladder" if jump == "ladder" else "avoided_snake")
            # Compared with what actually happened.
            change = race_model.chances({**after, mover_id: final}, following)[mover_id] \
                - chances_after[mover_id]
        start = int(turn["before"]) if final == board.goal else lands_on
        swings.append(_swing(kind, mover_name, roll, start, final, jump, change))

    # -- next rolls (only for the newest turn while the game goes on) -------------
    next_for_player: dict[str, Any] | None = None
    next_for_next_player: dict[str, Any] | None = None
    if newest and game["status"] == "in_progress":
        if actual_final < board.goal:
            next_for_player, found = _next_roll(
                race_model, mover_name, mover_id, after, chances_after[mover_id]
            )
            swings.extend(found)
        if following != mover_id and after[following] < board.goal:
            next_for_next_player, found = _next_roll(
                race_model, name(players[following]), following, after,
                chances_after[following],
            )
            swings.extend(found)

    # By impact level, then squares (a goal counts as the largest), then kind.
    swings.sort(key=lambda item: (
        IMPACT_ORDER.index(item["impact"]),
        -(item["squares"] if item["squares"] is not None else board.goal),
        SWING_KINDS.index(item["kind"]),
        -abs(item["_change"]),
    ))
    for item in swings:
        del item["_change"]

    return {
        "reaction": reaction_level(
            actual_change, turn["jumpType"], actual_final >= board.goal,
            abs(actual_final - int(turn["normalPosition"])) / board.goal,
        ),
        "nextForPlayer": next_for_player,
        "nextForNextPlayer": next_for_next_player,
        "biggestSwings": swings[:MAX_SWINGS],
        "race": _race_facts(
            players, name, mover_id, before, after, chances_before, chances_after
        ),
        "previousRoll": _previous_roll(board, game, turn_index, seating, name),
        "gameFlow": _game_flow(turns[: turn_index + 1], players, seating, name),
    }


def _race_facts(
    players: dict[str, dict[str, Any]],
    name: NamePlayer,
    mover_id: str,
    before: dict[str, int],
    after: dict[str, int],
    chances_before: dict[str, float],
    chances_after: dict[str, float],
) -> dict[str, Any]:
    leader_before, leader_after = _leader(before), _leader(after)
    others = [player_id for player_id in after if player_id != mover_id]
    # Passing someone still at the start is not worth a mention.
    overtook = [
        name(players[other]) for other in others
        if 0 < before[other] and before[mover_id] <= before[other] < after[mover_id]
    ]
    fell_behind = [
        name(players[other]) for other in others
        if after[mover_id] < after[other] <= before[mover_id]
    ]
    ranking = sorted(after.values(), reverse=True)
    top_before = max(chances_before.values(), default=0.0)
    top_after = max(chances_after.values(), default=0.0)
    phase_before, phase = phase_of(top_before), phase_of(top_after)
    if abs(top_after - top_before) < PHASE_CHANGE_MIN:
        # Hovering at a boundary is not a change in the race.
        phase_before = phase
    return {
        "leaderBefore": name(players[leader_before]) if leader_before else None,
        "leaderAfter": name(players[leader_after]) if leader_after else None,
        "leadChanged": leader_before != leader_after and leader_after is not None,
        "overtook": overtook,
        "fellBehind": fell_behind,
        # Squares the mover is behind the leader (0 when leading or tied for the lead).
        "behindLeader": ranking[0] - after[mover_id],
        # Squares the mover leads the next player by (only when leading alone).
        "leadMargin": (
            after[mover_id] - ranking[1]
            if len(ranking) > 1 and leader_after == mover_id else None
        ),
        "phaseBefore": phase_before,
        "phase": phase,
        # Chance of finishing first, for judgement only (never to be said as a number).
        "winChances": [
            {
                "player": name(players[player_id]),
                "word": chance_word(chance),
                "percent": round(chance * 100),
                "percentBefore": round(chances_before.get(player_id, 0.0) * 100),
            }
            for player_id, chance in sorted(
                chances_after.items(), key=lambda item: -item[1]
            )
        ],
    }


def _previous_roll(
    board: Board, game: dict[str, Any], turn_index: int, seating: list[str],
    name: NamePlayer,
) -> dict[str, Any] | None:
    """The roll just before this one and how much it mattered to its player."""
    if turn_index == 0:
        return None
    turns = game["turns"]
    previous = turns[turn_index - 1]
    players = {player["id"]: player for player in game["players"]}
    after = _positions_after(turns[:turn_index], seating)
    before = {**after, previous["playerId"]: int(previous["before"])}
    finished = {
        player_id for player_id, square in before.items()
        if square >= board.goal and player_id != previous["playerId"]
    }
    race_model = _Race(board, seating, finished)
    following = race_model.following(previous["playerId"])
    change = (
        race_model.chances(after, following)[previous["playerId"]]
        - race_model.chances(before, previous["playerId"])[previous["playerId"]]
    )
    return {
        "player": name(players[previous["playerId"]]),
        "roll": previous["roll"],
        "from": previous["before"],
        "final": previous["finalPosition"],
        "jump": previous["jumpType"],
        "reaction": reaction_level(
            change, previous["jumpType"], int(previous["finalPosition"]) >= board.goal,
            abs(int(previous["finalPosition"]) - int(previous["normalPosition"])) / board.goal,
        ),
    }


def _leader(positions: dict[str, int]) -> str | None:
    """The single player furthest ahead; None for a tie or when nobody has moved."""
    if not positions:
        return None
    top = max(positions.values())
    leaders = [player for player, position in positions.items() if position == top]
    return leaders[0] if len(leaders) == 1 and top > 0 else None


def _positions_after(turns: list[dict[str, Any]], seating: list[str]) -> dict[str, int]:
    positions = {player_id: 0 for player_id in seating}
    for turn in turns:
        positions[turn["playerId"]] = int(turn["finalPosition"])
    return positions


def _game_flow(
    turns: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    seating: list[str],
    name: NamePlayer,
) -> dict[str, Any]:
    """How the game has gone up to and including the last of ``turns``."""
    positions = {player_id: 0 for player_id in seating}
    leader: str | None = None
    lead_changes = 0
    leading_since: int | None = None
    biggest: dict[str, Any] | None = None
    for turn in turns:
        positions[turn["playerId"]] = int(turn["finalPosition"])
        current = _leader(positions)  # a tie keeps the last single leader
        if current is not None and current != leader:
            if leader is not None:
                lead_changes += 1
            leader = current
            leading_since = int(turn["round"])
        if turn["jumpType"]:
            squares = abs(int(turn["finalPosition"]) - int(turn["normalPosition"]))
            if biggest is None or squares > biggest["squares"]:
                biggest = {
                    "player": name(players[turn["playerId"]]),
                    "turnNumber": int(turn["round"]),
                    "jump": turn["jumpType"],
                    "from": int(turn["normalPosition"]),
                    "to": int(turn["finalPosition"]),
                    "squares": squares,
                }
    return {
        "rollsSoFar": len(turns),
        "leadChanges": lead_changes,
        # The turn number since which the current leader has led (None in a tie).
        "leaderSinceTurn": leading_since if _leader(positions) is not None else None,
        "biggestJumpSoFar": biggest,
    }
