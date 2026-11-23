"""commentary-v8: v7, with like chances grouped (2026-11-23).

The 48 and 49 snakes next to each other were always told as "the 49 snake": the
facts (``outlook_v8.py``) now put the same kind of swing for one player into one
item with ``alsoWith``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .v7 import system_prompt as v7_system_prompt

if TYPE_CHECKING:
    from ..settings import CommentarySettings

GROUPED_JA = [
    "まとめられた展開:",
    "- biggestSwings の項目に alsoWith があるときは、同じ人の同じ種類の展開がほかにもある"
    "（例: 2なら48のヘビ、3なら49のヘビ）。1つの話題として、"
    "「2か3でヘビ」のようにまとめて言ってよい。",
]
GROUPED_EN = [
    "Grouped swings:",
    "- An item of biggestSwings with alsoWith has more swings of the same kind for the same "
    "player (a 2 means the 48 snake, a 3 the 49 snake). Treat them as one topic and you may say "
    "them together (\"a 2 or a 3 means a snake\").",
]


def system_prompt(settings: CommentarySettings) -> str:
    extra = GROUPED_JA if settings.language == "ja" else GROUPED_EN
    return "\n".join([v7_system_prompt(settings), *extra])
