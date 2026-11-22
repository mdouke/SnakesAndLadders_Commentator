"""Exact chances of reaching the goal first, from the board alone.

Players do not affect each other's moves, so each player's number of rolls to
reach the goal is an independent random variable that depends only on their
square. ``survival(board)[t][square]`` is the chance of not having reached the
goal after ``t`` rolls from ``square``. Combining these with the turn order
gives every player's chance of finishing first, without simulation:

    P(k wins) = sum_t P(T_k = t) * prod_{j before k} P(T_j > t) * prod_{j after k} P(T_j > t - 1)

where "before k" are the players who roll before k in each round (counting from
the player who rolls next).

These numbers are for the commentary model only; the prompt tells it to turn
them into words and never to say them.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from ...domain.board import Board

MAX_ROLLS = 400  # the chance of still playing after this many rolls is negligible


@lru_cache(maxsize=8)
def survival(board: Board) -> tuple[tuple[float, ...], ...]:
    """``[t][square]``: chance of not having reached the goal after ``t`` rolls."""
    goal = board.goal
    moves = [
        [board.move(square, roll).final_position for roll in range(1, 7)]
        for square in range(goal)
    ]
    current = [1.0] * goal + [0.0]
    table = [tuple(current)]
    for _ in range(MAX_ROLLS):
        current = [
            sum(current[target] for target in moves[square]) / 6 for square in range(goal)
        ] + [0.0]
        table.append(tuple(current))
    return tuple(table)


def win_chances(board: Board, order: Sequence[tuple[str, int]]) -> dict[str, float]:
    """Each player's chance of reaching the goal first.

    ``order`` lists the players still racing as ``(id, square)``, starting with
    the player who rolls next. A player already on the goal square has won.
    """
    if not order:
        return {}
    for player_id, square in order:
        if square >= board.goal:
            return {other: 1.0 if other == player_id else 0.0 for other, _ in order}
    table = survival(board)
    chances: dict[str, float] = {}
    for index, (player_id, square) in enumerate(order):
        total = 0.0
        for rolls in range(1, MAX_ROLLS + 1):
            finish = table[rolls - 1][square] - table[rolls][square]
            if finish <= 0.0:
                continue
            others = 1.0
            for other_index, (_, other_square) in enumerate(order):
                if other_index < index:
                    others *= table[rolls][other_square]
                elif other_index > index:
                    others *= table[rolls - 1][other_square]
            total += finish * others
        chances[player_id] = total
    whole = sum(chances.values())
    return {player_id: value / whole for player_id, value in chances.items()} if whole else chances
