"""What could have happened and what may happen next, computed from the board.

The commentary model is told to use only given facts, so everything it may say
about possibilities is computed here with the game's own movement rule
(``Board.move``): the other rolls of this turn, the next roll of the player who
just moved and of the player who moves next, the race between players, and how
the game has gone so far.

The "size" of a swing is how many squares a ladder or a snake moves a player.
``biggest_swings`` lists them largest first, so the comment can lead with the
biggest one without the model having to compare numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...domain.board import Board

MAX_SWINGS = 5

# Kinds of swings, in the order they are preferred when two are the same size:
# what actually happened first, then what this roll could have done, then what
# the next rolls may do.
SWING_KINDS = (
    "hit_ladder", "hit_snake", "missed_ladder", "avoided_snake", "next_ladder", "next_snake",
)


def _leader(positions: dict[str, int]) -> str | None:
    """The single player furthest ahead; None for a tie or when nobody has moved."""
    if not positions:
        return None
    top = max(positions.values())
    leaders = [player for player, position in positions.items() if position == top]
    return leaders[0] if len(leaders) == 1 and top > 0 else None


def _roll_outcomes(board: Board, position: int) -> list[dict[str, Any]]:
    outcomes = []
    for roll in range(1, 7):
        result = board.move(position, roll)
        outcome: dict[str, Any] = {
            "roll": roll,
            "landsOn": result.normal_position,
            "final": result.final_position,
            "jump": result.jump.type.value if result.jump else None,
        }
        if result.overshoot:
            outcome["overshootStaysPut"] = True  # past the goal: the piece stays
        outcomes.append(outcome)
    return outcomes


def _next_roll(board: Board, name: str, position: int) -> dict[str, Any]:
    """Ladders, snakes and goal within one roll from ``position``."""
    ladders: list[dict[str, Any]] = []
    snakes: list[dict[str, Any]] = []
    goal_rolls: list[int] = []
    for outcome in _roll_outcomes(board, position):
        if outcome["final"] == board.goal:
            goal_rolls.append(outcome["roll"])
        if outcome["jump"] is None:
            continue
        item = {
            "roll": outcome["roll"],
            "from": outcome["landsOn"],
            "to": outcome["final"],
            "squares": abs(outcome["final"] - outcome["landsOn"]),
        }
        (ladders if outcome["jump"] == "ladder" else snakes).append(item)
    return {
        "player": name,
        "position": position,
        "ladderChances": ladders,
        "snakeRisks": snakes,
        "goalRolls": goal_rolls,
    }


def analyze_turn(
    game: dict[str, Any], turn_index: int, name: Callable[[dict[str, Any]], str]
) -> dict[str, Any]:
    """Facts about possibilities and the race for ``game["turns"][turn_index]``.

    ``name`` gives the name the prompt uses for a player payload.
    """
    board = Board.from_dict(game["board"])
    players = {player["id"]: player for player in game["players"]}
    turns = game["turns"]
    turn = turns[turn_index]
    mover = players[turn["playerId"]]
    mover_name = name(mover)
    newest = turn_index == len(turns) - 1

    # -- this roll: every roll from the square before it --------------------------
    this_roll = []
    swings: list[dict[str, Any]] = []
    for outcome in _roll_outcomes(board, int(turn["before"])):
        actual = outcome["roll"] == turn["roll"]
        this_roll.append({**outcome, "actual": actual})
        if outcome["jump"] is None:
            continue
        ladder = outcome["jump"] == "ladder"
        if actual:
            kind = "hit_ladder" if ladder else "hit_snake"
        else:
            kind = "missed_ladder" if ladder else "avoided_snake"
        swings.append({
            "kind": kind, "player": mover_name, "roll": outcome["roll"],
            "from": outcome["landsOn"], "to": outcome["final"],
            "squares": abs(outcome["final"] - outcome["landsOn"]),
        })

    # -- next rolls (only for the newest turn; later turns changed the board) ------
    next_for_player: dict[str, Any] | None = None
    next_for_next_player: dict[str, Any] | None = None
    if newest and game["status"] == "in_progress":
        mover_position = int(mover["position"])
        if not mover["reachedGoal"]:
            next_for_player = _next_roll(board, mover_name, mover_position)
        upcoming = players.get(game.get("currentPlayerId") or "")
        if upcoming is not None and upcoming["id"] != mover["id"] and not upcoming["reachedGoal"]:
            next_for_next_player = _next_roll(board, name(upcoming), int(upcoming["position"]))
    for outlook in (next_for_player, next_for_next_player):
        if outlook is None:
            continue
        for kind, items in (("next_ladder", outlook["ladderChances"]),
                            ("next_snake", outlook["snakeRisks"])):
            swings.extend({"kind": kind, "player": outlook["player"], **item} for item in items)

    swings.sort(key=lambda item: (-item["squares"], SWING_KINDS.index(item["kind"])))

    # -- the race: positions just before and just after this roll -----------------
    # The newest turn uses the real positions (they include position corrections).
    after = (
        {player_id: int(player["position"]) for player_id, player in players.items()}
        if newest else _positions_after(turns[: turn_index + 1], players)
    )
    before = dict(after)
    before[mover["id"]] = int(turn["before"])
    leader_before, leader_after = _leader(before), _leader(after)
    others = [player_id for player_id in after if player_id != mover["id"]]
    # Passing someone still at the start is not worth a mention.
    overtook = [
        name(players[other]) for other in others
        if 0 < before[other] and before[mover["id"]] <= before[other] < after[mover["id"]]
    ]
    fell_behind = [
        name(players[other]) for other in others
        if after[mover["id"]] < after[other] <= before[mover["id"]]
    ]
    ranking = sorted(after.values(), reverse=True)
    mover_after = after[mover["id"]]
    race = {
        "leaderBefore": name(players[leader_before]) if leader_before else None,
        "leaderAfter": name(players[leader_after]) if leader_after else None,
        "leadChanged": leader_before != leader_after and leader_after is not None,
        "overtook": overtook,
        "fellBehind": fell_behind,
        # Squares the mover is behind the leader (0 when leading or tied for the lead).
        "behindLeader": ranking[0] - mover_after,
        # Squares the mover leads the next player by (only when leading alone).
        "leadMargin": (
            mover_after - ranking[1] if len(ranking) > 1 and leader_after == mover["id"] else None
        ),
    }

    return {
        "thisRollOptions": this_roll,
        "nextForPlayer": next_for_player,
        "nextForNextPlayer": next_for_next_player,
        "biggestSwings": swings[:MAX_SWINGS],
        "race": race,
        "gameFlow": _game_flow(turns[: turn_index + 1], players, name),
    }


def _positions_after(
    turns: list[dict[str, Any]], players: dict[str, dict[str, Any]]
) -> dict[str, int]:
    positions = {player_id: 0 for player_id in players}
    for turn in turns:
        positions[turn["playerId"]] = int(turn["finalPosition"])
    return positions


def _game_flow(
    turns: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    name: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """How the game has gone up to and including the last of ``turns``."""
    positions = {player_id: 0 for player_id in players}
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
