"""Every commentary prompt version, selectable in the settings.

Each version keeps its own system prompt and the extra facts it sends, so an
older version can be chosen again later (for example to compare with a newer
one in a study). A version is never changed after it was used: a change makes
a new version.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .. import outlook, outlook_v2, outlook_v4
from ..settings import LATEST_PROMPT_VERSION, PROMPT_VERSIONS
from . import v1, v2, v3, v4

if TYPE_CHECKING:
    from ..settings import CommentarySettings

Analyze = Callable[[dict[str, Any], int, Callable[[dict[str, Any]], str]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PromptVersion:
    id: str
    label: str
    description: str
    system_prompt: Callable[[CommentarySettings], str]
    # Extra facts computed for the turn (None: only the basic facts).
    analyze: Analyze | None
    # v1 called a roll past the goal "bounced back"; the rule keeps the piece in place.
    overshoot_key: str = "overshotGoalStayedPut"
    # How many of the last comments in the game to send (0: none).
    previous_comments: int = 0


VERSIONS: dict[str, PromptVersion] = {
    version.id: version
    for version in (
        PromptVersion(
            "commentary-v1", "v1：事実だけ",
            "そのターンの事実・全員の位置・直前6回の出目だけを渡し、事実どおりに実況する。",
            v1.system_prompt, None, overshoot_key="overshotGoalAndBouncedBack",
        ),
        PromptVersion(
            "commentary-v2", "v2：大きな展開（マス数）",
            "起こり得たこと・次の可能性・勝負の状況を加え、移動マス数の大きい展開を優先する。",
            v2.system_prompt, outlook_v2.analyze_turn,
        ),
        PromptVersion(
            "commentary-v3", "v3：勝つ確率・反応・中立",
            "勝つ確率の変化で展開の大きさを測り、反応の強さ・局面・直前の出目を加える。"
            "中立で、勝つ確率は言葉だけで伝える。",
            v3.system_prompt, outlook.analyze_turn,
        ),
        PromptVersion(
            "commentary-v4", "v4：感情・流れ・繰り返さない",
            "惜しい・危ないにも感情を込め、直前の実況を踏まえて繰り返さない。"
            "出目と移動を毎回は言わず、序盤は「始まったばかり」、続くチャンスも伝える。",
            v4.system_prompt, outlook_v4.analyze_turn, previous_comments=5,
        ),
    )
}
assert tuple(VERSIONS) == PROMPT_VERSIONS and LATEST_PROMPT_VERSION in VERSIONS


def get_version(version_id: str) -> PromptVersion:
    return VERSIONS.get(version_id) or VERSIONS[LATEST_PROMPT_VERSION]


def version_list() -> list[dict[str, str]]:
    """For the admin screen."""
    return [
        {"id": version.id, "label": version.label, "description": version.description}
        for version in VERSIONS.values()
    ]
