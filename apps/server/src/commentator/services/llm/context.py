"""Structured turn context and the prompt built from it.

The context holds only facts computed by the game engine. The prompt tells the
model to use these facts as they are, so a model can phrase the commentary but
cannot change what happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .settings import CommentarySettings

PROMPT_VERSION = "commentary-v1"
RECENT_TURNS = 6

# Target and hard maximum length per setting: characters for Japanese, words for
# English. Texts over the maximum are cut at a sentence end (see safety.py).
LENGTH_LIMITS: dict[str, dict[str, tuple[int, int]]] = {
    "ja": {"short": (40, 70), "medium": (80, 130), "long": (140, 210)},
    "en": {"short": (20, 35), "medium": (40, 65), "long": (70, 105)},
}


@dataclass(frozen=True, slots=True)
class PlayerFact:
    name: str
    order: int
    position: int
    rank: int | None
    reached_goal: bool
    ladders: int
    snakes: int


@dataclass(frozen=True, slots=True)
class TurnFact:
    round: int
    player: str
    roll: int
    before: int
    normal_position: int
    final_position: int
    overshoot: bool
    jump_type: str | None


@dataclass(frozen=True, slots=True)
class CommentaryContext:
    """Everything one commentary request may talk about."""

    game_id: str
    turn_id: str
    state_version: int
    turn_number: int
    player_name: str
    roll: int
    before: int
    normal_position: int
    final_position: int
    overshoot: bool
    jump_type: str | None
    events: tuple[str, ...]
    goal: int
    leader_name: str | None
    game_finished: bool = False
    players: tuple[PlayerFact, ...] = field(default_factory=tuple)
    recent_turns: tuple[TurnFact, ...] = field(default_factory=tuple)

    def facts(self) -> dict[str, Any]:
        """JSON-ready facts for the prompt; keys are English and self-explanatory."""
        return {
            "turn": {
                "turnNumber": self.turn_number,
                "player": self.player_name,
                "roll": self.roll,
                "from": self.before,
                "afterRoll": self.normal_position,
                "final": self.final_position,
                "overshotGoalAndBouncedBack": self.overshoot,
                "jump": self.jump_type,
                "events": list(self.events),
            },
            "goalSquare": self.goal,
            "leader": self.leader_name,
            "gameFinished": self.game_finished,
            "players": [
                {
                    "name": player.name,
                    "turnOrder": player.order,
                    "position": player.position,
                    "rank": player.rank,
                    "reachedGoal": player.reached_goal,
                    "laddersClimbed": player.ladders,
                    "snakesHit": player.snakes,
                }
                for player in self.players
            ],
            "recentTurns": [
                {
                    "turnNumber": turn.round,
                    "player": turn.player,
                    "roll": turn.roll,
                    "from": turn.before,
                    "final": turn.final_position,
                    "jump": turn.jump_type,
                    "overshoot": turn.overshoot,
                }
                for turn in self.recent_turns
            ],
        }


@dataclass(frozen=True, slots=True)
class CommentaryPrompt:
    version: str
    system: str
    user: str
    context: CommentaryContext
    settings: CommentarySettings

    @property
    def max_length(self) -> int:
        return LENGTH_LIMITS[self.settings.language][self.settings.length][1]


def player_label(order: int, language: str) -> str:
    return f"プレイヤー{order}" if language == "ja" else f"Player {order}"


def build_context(
    game: dict[str, Any], turn_id: str, settings: CommentarySettings
) -> CommentaryContext:
    """Build the context from a game payload (``GameState.to_dict()``)."""
    order = {player["id"]: index + 1 for index, player in enumerate(game["players"])}

    def name(player: dict[str, Any]) -> str:
        if settings.use_names:
            return str(player["displayName"])
        return player_label(order[player["id"]], settings.language)

    players_by_id = {player["id"]: player for player in game["players"]}
    turn = next(item for item in game["turns"] if item["id"] == turn_id)
    player = players_by_id[turn["playerId"]]
    positions = [item["position"] for item in game["players"]]
    top = max(positions, default=0)
    leaders = [item for item in game["players"] if item["position"] == top]
    index = next(i for i, item in enumerate(game["turns"]) if item["id"] == turn_id)
    recent = game["turns"][max(0, index - RECENT_TURNS):index]
    return CommentaryContext(
        game_id=game["id"],
        turn_id=turn["id"],
        state_version=int(game["stateVersion"]),
        turn_number=turn["round"],  # the turn number shown on screen
        player_name=name(player),
        roll=turn["roll"],
        before=turn["before"],
        normal_position=turn["normalPosition"],
        final_position=turn["finalPosition"],
        overshoot=turn["overshoot"],
        jump_type=turn["jumpType"],
        events=tuple(turn["events"]),
        goal=int(game["board"]["goal"]),
        # A tie has no single leader.
        leader_name=name(leaders[0]) if len(leaders) == 1 and top > 0 else None,
        game_finished=game["status"] == "completed",
        players=tuple(
            PlayerFact(
                name=name(item),
                order=order[item["id"]],
                position=item["position"],
                rank=item["rank"] if item["rankConfirmed"] else None,
                reached_goal=item["reachedGoal"],
                ladders=item["statistics"]["ladderCount"],
                snakes=item["statistics"]["snakeCount"],
            )
            for item in game["players"]
        ),
        recent_turns=tuple(
            TurnFact(
                round=item["round"],
                player=name(players_by_id[item["playerId"]]),
                roll=item["roll"],
                before=item["before"],
                normal_position=item["normalPosition"],
                final_position=item["finalPosition"],
                overshoot=item["overshoot"],
                jump_type=item["jumpType"],
            )
            for item in recent
        ),
    )


_STYLE_JA = {
    "lively": "テンポのよいスポーツ実況のように、明るく盛り上げる。",
    "calm": "落ち着いた解説者のように、穏やかで聞き取りやすく話す。",
    "kids": "小さな子どもにも分かるやさしい言葉で、楽しく話す。",
}
_STYLE_EN = {
    "lively": "Energetic, like a sports play-by-play announcer.",
    "calm": "Calm and clear, like a relaxed color commentator.",
    "kids": "Simple, friendly words that young children understand.",
}


def build_system_prompt(settings: CommentarySettings) -> str:
    """The instructions for the model; they depend on the settings only."""
    target, maximum = LENGTH_LIMITS[settings.language][settings.length]
    if settings.language == "ja":
        names = (
            "プレイヤーは JSON の名前のとおりに呼び、敬称は「さん」を付ける。"
            if settings.use_names
            else "プレイヤーは JSON にある「プレイヤー1」などの呼び方だけを使う。"
        )
        return "\n".join([
            "あなたは、実物のボードゲーム「ヘビとハシゴ」を遊んでいる人たちのそばで、"
            "ターンごとに短い実況をする実況者です。",
            "守ること:",
            "- 与えられた JSON の事実だけを使う。数字、マス、順位を変えたり、"
            "起きていない出来事を付け足したりしない。",
            f"- 長さは日本語で{target}字程度、多くても{maximum}字以内にする。",
            "- 出力は実況の文章だけにする。前置き、かぎかっこ、箇条書き、記号の飾り、"
            "絵文字、Markdown は使わない。",
            "- 音声で読み上げるので、耳で聞いて分かる自然な話し言葉にする。",
            "- プレイヤーをからかったり傷つけたりする言葉、乱暴な言葉は使わない。"
            "悪い結果でも前向きに伝える。",
            f"- {names}",
            "- jump が ladder ならハシゴを登った、snake ならヘビで下がった、"
            "overshotGoalAndBouncedBack が true ならゴールを超えて戻ったことを伝える。",
            f"話し方: {_STYLE_JA[settings.style]}",
        ])
    names = (
        "Call players exactly by the names in the JSON."
        if settings.use_names
        else 'Refer to players only as "Player 1" etc., as given in the JSON.'
    )
    return "\n".join([
        "You are a live commentator standing next to people playing the physical "
        "board game Snakes and Ladders. You give one short comment per turn.",
        "Rules:",
        "- Use only the facts in the JSON. Never change numbers, squares or ranks, "
        "and never add events that did not happen.",
        f"- About {target} words, never more than {maximum} words.",
        "- Output only the comment itself: no preface, quotes, lists, emoji or Markdown.",
        "- It will be read aloud, so write natural spoken language.",
        "- Never tease or hurt players and never use rude words. Stay upbeat even "
        "after bad luck.",
        f"- {names}",
        "- jump = ladder means they climbed a ladder, snake means a snake took them "
        "down, and overshotGoalAndBouncedBack means they passed the goal and bounced "
        "back.",
        f"Style: {_STYLE_EN[settings.style]}",
    ])


def build_user_prompt(context: CommentaryContext, settings: CommentarySettings) -> str:
    """This turn's facts as JSON, with a one-line request."""
    facts = json.dumps(context.facts(), ensure_ascii=False, indent=1)
    if settings.language == "ja":
        return f"このターンの状況です。実況を1つ作ってください。\n{facts}"
    return f"The situation of this turn. Write one comment.\n{facts}"


def build_prompt(context: CommentaryContext, settings: CommentarySettings) -> CommentaryPrompt:
    system = build_system_prompt(settings)
    user = build_user_prompt(context, settings)
    return CommentaryPrompt(
        version=PROMPT_VERSION, system=system, user=user, context=context, settings=settings
    )
