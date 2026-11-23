"""commentary-v6: v5, plus what to keep and what to cut first (2026-11-23).

With the length set to "short" (about 40 characters), v5 kept most topics but
sometimes chose a smaller danger over the biggest one, or dropped the change in
the race. v6 says what must stay and in which order words are cut; saying the
same thing twice goes first. Its facts are v5's (``outlook_v5.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .v4 import system_prompt as v4_system_prompt

if TYPE_CHECKING:
    from ..settings import CommentarySettings

KEEP_JA = [
    "短くまとめる:",
    "- 必ず残す: 感嘆、biggestSwings の先頭（いちばん大きな展開）、"
    "局面の変化（phaseBefore から変わったとき、逆転・追い越し）。",
    "- 今回の出目で実際に起きたハシゴ・ヘビ・ゴールは、影響が小さくても、"
    "何が起きてどこへ行ったか（「ヘビで26へ」など）を必ず言う。「後退」のようにぼかさない。",
    "- 長さに収まらないときは、次の順に削る: "
    "同じことの言い直し（同じ出来事を2回言わない）"
    "→ 途中のマス目（「51の」「49の」など。行き先のマス目は残す）→ 差のマス数。",
    "- 小さい展開のために大きい展開を削らない。",
]
KEEP_EN = [
    "Keeping it short:",
    "- Always keep: the exclamation, the top of biggestSwings (the biggest swing), and a change in "
    "the race (a change from phaseBefore, a lead change, overtaking).",
    "- A ladder, snake or goal this roll actually hit is always told plainly, even when its impact "
    "is small: what happened and where the piece went (\"slid down the snake to 26\").",
    "- When it does not fit, cut in this order: saying the same thing twice (never tell one event "
    "twice), then the squares on the way (keep where the piece ends up), then the gap in squares.",
    "- Never drop a bigger swing to make room for a smaller one.",
]


def system_prompt(settings: CommentarySettings) -> str:
    """v4's instructions (the same as v5's) with the keep-and-cut rules."""
    extra = KEEP_JA if settings.language == "ja" else KEEP_EN
    return "\n".join([v4_system_prompt(settings), *extra])
