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

from .. import outlook, outlook_v2, outlook_v4, outlook_v5, outlook_v7
from ..settings import LATEST_PROMPT_VERSION, PROMPT_VERSIONS
from . import v1, v2, v3, v4, v5, v6, v7

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
        PromptVersion(
            "commentary-v5", "v5：v4＋実際の結果を優先",
            "v4 と同じ指示。ハシゴ・ヘビ・ゴールが実際に起きた出目では、"
            "避けたヘビなど「起き得たこと」より実際の結果の感情（喜び・落胆）を優先する。",
            v5.system_prompt, outlook_v5.analyze_turn, previous_comments=5,
        ),
        PromptVersion(
            "commentary-v6", "v6：v5＋短くするときの残し方",
            "v5 に、必ず残すもの（感嘆・いちばん大きな展開・局面の変化）と削る順番"
            "（言い直し→途中のマス目→差のマス数）を加える。長さ「短め」と組み合わせて使う。",
            v6.system_prompt, outlook_v5.analyze_turn, previous_comments=5,
        ),
        PromptVersion(
            "commentary-v7", "v7：v6＋通り過ぎた／届かない",
            "v6 に加え、惜しかったハシゴ・ゴール・避けたヘビが、出目が大きすぎて通り過ぎたのか、"
            "小さくて届かなかったのかを渡し、言い分ける。",
            v7.system_prompt, outlook_v7.analyze_turn, previous_comments=5,
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
