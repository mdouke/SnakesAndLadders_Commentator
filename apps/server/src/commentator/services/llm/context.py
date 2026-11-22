"""Structured turn context and the prompt built from it.

The context holds only facts computed by the game engine. The prompt tells the
model to use these facts as they are, so a model can phrase the commentary but
cannot change what happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .prompts import get_version
from .prompts.common import LENGTH_LIMITS
from .settings import CommentarySettings

RECENT_TURNS = 6

__all__ = ["LENGTH_LIMITS", "CommentaryContext", "CommentaryPrompt", "build_context",
           "build_prompt", "build_system_prompt", "build_user_prompt"]


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
    # Extra facts of the prompt version (see prompts/), already in prompt form.
    outlook: dict[str, Any] = field(default_factory=dict)
    overshoot_key: str = "overshotGoalStayedPut"

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
                self.overshoot_key: self.overshoot,
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
            **self.outlook,
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
    version = get_version(settings.prompt_version)
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
        outlook=version.analyze(game, index, name) if version.analyze else {},
        overshoot_key=version.overshoot_key,
    )


def build_system_prompt(settings: CommentarySettings) -> str:
    """The instructions for the model; they depend on the settings only."""
    return get_version(settings.prompt_version).system_prompt(settings)


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
        version=settings.prompt_version, system=system, user=user, context=context,
        settings=settings,
    )
