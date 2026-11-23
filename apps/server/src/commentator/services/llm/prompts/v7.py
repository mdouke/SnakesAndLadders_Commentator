"""commentary-v7: v6, plus "went past" or "fell short" for near misses (2026-11-23).

v6 said "fell short!" for a 5 that went past the ladder a 2 would have hit.
The facts (``outlook_v7.py``) now say which way each near miss went.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .v6 import system_prompt as v6_system_prompt

if TYPE_CHECKING:
    from ..settings import CommentarySettings

DIRECTION_JA = [
    "惜しかった場面の言い方:",
    "- missed_goal / missed_ladder / avoided_snake と、"
    "about がそれらの emotion には direction がある。"
    "passed は出目が大きすぎて通り過ぎた（「通り過ぎた」「行き過ぎた」）、short は出目が小さくて"
    "届かなかった（「届かない」「あと一歩」）。逆の言い方をしない。byPips は何目ずれたか。",
]
DIRECTION_EN = [
    "Near misses:",
    "- missed_goal / missed_ladder / avoided_snake, and an emotion about one of them, have a "
    "direction: passed means the roll was too big and went past it (\"went right past it\"), "
    "short means it was too small (\"just short\"). Never say the opposite. byPips is how many "
    "pips it was off by.",
]


def system_prompt(settings: CommentarySettings) -> str:
    extra = DIRECTION_JA if settings.language == "ja" else DIRECTION_EN
    return "\n".join([v6_system_prompt(settings), *extra])
