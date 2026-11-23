"""Possibilities, impact and the race given to the commentary model.

Covers llm/winning.py (exact chances of finishing first) and llm/outlook.py.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from commentator.domain.board import Board, Jump, JumpType
from commentator.domain.game import GameState, PlayerState
from commentator.presets import load_board_preset
from commentator.services.llm.context import build_context, build_prompt
from commentator.services.llm.outlook import (
    impact_level,
    phase_of,
    reaction_level,
)
from commentator.services.llm.settings import CommentarySettings
from commentator.services.llm.winning import win_chances

# A small board: ladders 3->20 and 9->12, snakes 7->1 and 27->10, goal 30.
BOARD = Board("test", "Test", 30, (
    Jump(JumpType.LADDER, 3, 20),
    Jump(JumpType.LADDER, 9, 12),
    Jump(JumpType.SNAKE, 7, 1),
    Jump(JumpType.SNAKE, 27, 10),
))
SETTINGS = CommentarySettings(provider="claude", prompt_version="commentary-v3")
LEVELS = ["decisive", "big", "notable", "small"]


def play(*rolls: int, board: Board = BOARD) -> dict[str, Any]:
    game = GameState.create(board, [PlayerState("a", "あおい", "red"),
                                    PlayerState("b", "はると", "blue")])
    game.start()
    for value in rolls:
        game.roll(value)
    return game.to_dict()


def outlook(game: dict[str, Any]) -> dict[str, Any]:
    return build_context(game, game["turns"][-1]["id"], SETTINGS).outlook


def kinds(result: dict[str, Any]) -> list[tuple[str, str, int]]:
    return [(item["kind"], item["player"], item["roll"]) for item in result["biggestSwings"]]


# -- winning.py ---------------------------------------------------------------------


def test_win_chances_match_a_simulation() -> None:
    board = load_board_preset()
    exact = win_chances(board, [("orange", 85), ("blue", 19)])
    assert sum(exact.values()) == pytest.approx(1.0)
    random.seed(1)
    wins = 0
    for _ in range(4000):
        squares = {"orange": 85, "blue": 19}
        while True:
            squares["orange"] = board.move(squares["orange"], random.randint(1, 6)).final_position
            if squares["orange"] == board.goal:
                wins += 1
                break
            squares["blue"] = board.move(squares["blue"], random.randint(1, 6)).final_position
            if squares["blue"] == board.goal:
                break
    assert exact["orange"] == pytest.approx(wins / 4000, abs=0.03)


def test_rolling_first_is_a_small_advantage_and_the_goal_wins() -> None:
    first = win_chances(BOARD, [("a", 0), ("b", 0)])
    assert first["a"] > first["b"]
    assert win_chances(BOARD, [("a", 5), ("b", 30)]) == {"a": 0.0, "b": 1.0}


# -- impact and labels --------------------------------------------------------------


def test_labels() -> None:
    assert [impact_level(change) for change in (0.3, -0.15, 0.06, 0.01)] == LEVELS
    assert reaction_level(0.25, None, False) == "huge_cheer"
    assert reaction_level(-0.1, "snake", False) == "groan"
    assert reaction_level(0.01, "ladder", False) == "mild_cheer"
    # A long ladder is a big moment even when the chances barely move.
    assert reaction_level(0.03, "ladder", False, size=0.56) == "huge_cheer"
    assert reaction_level(-0.02, "snake", False, size=0.2) == "groan"
    assert reaction_level(0.0, None, True) == "huge_cheer"
    assert [phase_of(value) for value in (0.9, 0.7, 0.5)] == [
        "nearly_decided", "pulling_away", "close",
    ]


def test_swings_are_sorted_by_impact() -> None:
    result = outlook(play(1))
    levels = [LEVELS.index(item["impact"]) for item in result["biggestSwings"]]
    assert levels == sorted(levels)
    assert ("missed_ladder", "あおい", 3) in kinds(result)
    assert ("next_ladder", "はると", 3) in kinds(result)


def test_near_the_goal_a_short_snake_outranks_a_longer_one_far_behind() -> None:
    # Standard board, 21 rolls: あおい ends on 47, はると waits on 96. はると's 2
    # would hit 98->78 (20 squares) and 4 would reach the goal; あおい's next 2
    # would hit 49->11 (38 squares) but she is far behind, so it matters less,
    # and her ladder 51->67 matters more than that snake.
    rolls = (5, 2, 5, 6, 4, 6, 5, 4, 2, 6, 5, 2, 5, 2, 5, 1, 5, 6, 2, 5, 5)
    result = outlook(play(*rolls, board=load_board_preset()))
    ranked = kinds(result)
    assert ranked[:2] == [("next_goal", "はると", 4), ("next_snake", "はると", 2)]
    impact = {(item["kind"], item["player"], item["roll"]): item["impact"]
              for item in result["biggestSwings"]}
    assert impact[("next_snake", "はると", 2)] == "big"
    assert LEVELS.index(impact[("next_ladder", "あおい", 4)]) < LEVELS.index(
        impact.get(("next_snake", "あおい", 2), "small")
    )


def test_a_hit_ladder_gets_a_cheer_and_a_hit_snake_a_groan() -> None:
    climbed = outlook(play(3))
    assert climbed["reaction"] in {"huge_cheer", "cheer"}
    assert ("hit_ladder", "あおい", 3) in kinds(climbed)
    slid = outlook(play(5, 1, 2))  # あおい 5 -> 7, snake to 1
    assert slid["reaction"] in {"groan", "huge_groan", "mild_groan"}
    assert ("hit_snake", "あおい", 2) in kinds(slid)


def test_next_rolls_for_both_players_and_goal_rolls() -> None:
    result = outlook(play(1))
    assert result["nextForPlayer"]["player"] == "あおい"
    assert result["nextForNextPlayer"]["player"] == "はると"
    assert result["nextForNextPlayer"]["ladderChances"] == [
        {"roll": 3, "from": 3, "to": 20, "squares": 17},
    ]
    near = outlook(play(3, 1, 6, 1, 2))  # あおい 20 -> 26 -> 28
    assert near["nextForPlayer"]["goalRolls"] == [2]
    goal = next(item for item in near["biggestSwings"] if item["kind"] == "next_goal")
    assert (goal["from"], goal["to"], goal["squares"]) == (28, 30, None)


# -- race, previous roll and game flow -------------------------------------------------


def test_race_reports_a_lead_change_and_win_chances() -> None:
    result = outlook(play(2, 4, 4))  # あおい 2; はると 4; あおい 2 -> 6, passing はると
    race = result["race"]
    assert race["leaderBefore"] == "はると" and race["leaderAfter"] == "あおい"
    assert race["leadChanged"] is True and race["overtook"] == ["はると"]
    assert race["leadMargin"] == 2
    chances = {item["player"]: item for item in race["winChances"]}
    assert set(chances) == {"あおい", "はると"}
    assert chances["あおい"]["percent"] + chances["はると"]["percent"] in {99, 100, 101}
    assert race["phase"] in {"close", "pulling_away", "nearly_decided"}


def test_a_snake_can_lose_the_lead() -> None:
    race = outlook(play(6, 5, 1))["race"]  # あおい 6; はると 5; あおい 7 -> snake to 1
    assert race["leaderBefore"] == "あおい" and race["leaderAfter"] == "はると"
    assert race["fellBehind"] == ["はると"]
    assert race["behindLeader"] == 4


def test_previous_roll_carries_its_reaction() -> None:
    result = outlook(play(3, 1))  # あおい climbed 3->20 just before はると's roll
    previous = result["previousRoll"]
    assert previous["player"] == "あおい" and previous["jump"] == "ladder"
    assert previous["reaction"] in {"huge_cheer", "cheer", "mild_cheer"}
    assert outlook(play(1))["previousRoll"] is None


def test_biggest_jump_so_far_and_prompt_version() -> None:
    game = play(3, 1, 1)
    flow = outlook(game)["gameFlow"]
    assert flow["biggestJumpSoFar"]["squares"] == 17
    prompt = build_prompt(build_context(game, game["turns"][-1]["id"], SETTINGS), SETTINGS)
    assert prompt.version == "commentary-v3"
    assert '"biggestSwings"' in prompt.user and '"reaction"' in prompt.user
    assert "中立" in prompt.system and "パーセントで言わない" in prompt.system


def test_no_next_rolls_once_the_game_is_over() -> None:
    game = play(3, 1, 6, 1, 4)  # あおい 20 -> 26 -> 30: goal
    result = outlook(game)
    assert game["status"] == "completed"
    assert result["nextForPlayer"] is None and result["nextForNextPlayer"] is None
    assert result["reaction"] == "huge_cheer"
    assert ("reached_goal", "あおい", 4) in kinds(result)


# -- v4 -----------------------------------------------------------------------------

V4 = CommentarySettings(provider="claude", prompt_version="commentary-v4")


def outlook_v4(game: dict[str, Any], comments: tuple[str, ...] = ()) -> dict[str, Any]:
    return build_context(game, game["turns"][-1]["id"], V4, comments).facts()


def test_v4_opening_phase_until_everyone_rolled_twice() -> None:
    assert outlook_v4(play(1))["race"]["phase"] == "opening"
    assert outlook_v4(play(1, 1, 1))["race"]["phase"] == "opening"
    assert outlook_v4(play(1, 1, 1, 1))["race"]["phase"] != "opening"


def test_v4_emotion_for_a_missed_goal_and_a_hit_ladder() -> None:
    # From 26 a 2 lands on 28, but a 1 would have hit the 27 snake (to 10): relief.
    dodged = outlook_v4(play(3, 1, 6, 1, 2))
    assert "reaction" not in dodged
    assert dodged["emotion"]["kind"] == "relief"
    assert dodged["emotion"]["about"] == "avoided_snake"
    # From 28 a 1 lands on 29, while a 2 would have reached the goal (30).
    missed = outlook_v4(play(3, 1, 6, 1, 2, 1, 1))
    assert missed["emotion"]["kind"] == "so_close"
    assert missed["emotion"]["about"] == "missed_goal"
    climbed = outlook_v4(play(3))
    assert climbed["emotion"]["kind"] == "joy"
    assert climbed["emotion"]["strength"] in {"huge", "big"}


def test_v4_small_near_misses_are_not_a_moment() -> None:
    # あおい 0 -> 1 on the standard board: no big ladder or snake within one pip.
    facts = outlook_v4(play(6, board=load_board_preset()))
    assert facts["emotion"]["kind"] in {"calm", "joy", "so_close"}
    if facts["emotion"]["kind"] == "so_close":
        assert facts["emotion"]["strength"] in {"huge", "big"}


def test_v4_marks_a_chance_that_is_still_in_reach() -> None:
    # あおい 0 -> 1: a 3 would have climbed 3->20, and from 1 a 2 still does.
    swings = outlook_v4(play(1))["biggestSwings"]
    still = [item for item in swings if item.get("stillInReach")]
    assert [(item["player"], item["from"]) for item in still] == [("あおい", 3)]


def test_v4_sends_the_last_five_previous_comments_only() -> None:
    comments = tuple(f"実況{number}" for number in range(1, 8))
    game = play(1)
    facts = outlook_v4(game, comments)
    assert facts["previousComments"] == ["実況3", "実況4", "実況5", "実況6", "実況7"]
    v3 = build_context(game, game["turns"][-1]["id"], SETTINGS, comments).facts()
    assert "previousComments" not in v3  # v3 does not use them


# -- v5 ------------------------------------------------------------------------------

V5 = CommentarySettings(provider="claude", prompt_version="commentary-v5")
# Ladder 13->20 next to a bigger snake 12->1: from 7, a 6 climbs, a 5 would slide.
LADDER_BY_SNAKE = Board("test-v5", "Test", 40, (
    Jump(JumpType.LADDER, 13, 20),
    Jump(JumpType.SNAKE, 12, 1),
))


def test_v5_keeps_the_joy_of_a_ladder_climbed_past_a_bigger_snake() -> None:
    # 2026-11-23: v4 opened "Phew!" because the snake dodged (to 11) outranked
    # the ladder actually climbed (51 -> 67).
    game = play(6, 1, 1, 1, 6, board=LADDER_BY_SNAKE)
    assert game["turns"][-1]["jumpType"] == "ladder"
    v4 = build_context(game, game["turns"][-1]["id"], V4).facts()
    assert v4["emotion"]["kind"] == "relief"  # v4 is frozen as it was
    v5 = build_context(game, game["turns"][-1]["id"], V5).facts()
    assert v5["emotion"]["kind"] == "joy"
    assert v5["emotion"]["about"] == "actual"
    kinds_v5 = {item["kind"] for item in v5["biggestSwings"]}
    assert {"hit_ladder", "avoided_snake"} <= kinds_v5  # the dodge is still told


def test_v5_still_feels_near_misses_on_a_plain_move() -> None:
    # From 26 a 2 lands on 28 (no jump), while a 1 would have hit the 27 snake.
    game = play(3, 1, 6, 1, 2)
    facts = build_context(game, game["turns"][-1]["id"], V5).facts()
    assert facts["emotion"]["kind"] == "relief"


def test_v6_adds_keep_and_cut_rules_to_v5() -> None:
    from commentator.services.llm.prompts import get_version

    for language in ("ja", "en"):
        settings = CommentarySettings(provider="claude", language=language, length="short")
        v5 = get_version("commentary-v5").system_prompt(settings)
        v6 = get_version("commentary-v6").system_prompt(settings)
        assert v6.startswith(v5)
        assert len(v6) > len(v5)
    ja = get_version("commentary-v6").system_prompt(
        CommentarySettings(provider="claude", length="short")
    )
    # Saying the same thing twice is cut first.
    assert ja.index("言い直し") < ja.index("途中のマス目") < ja.index("差のマス数")


# -- v7 ------------------------------------------------------------------------------

V7 = CommentarySettings(provider="claude", prompt_version="commentary-v7")


def test_v7_says_whether_a_missed_ladder_was_passed_or_not_reached() -> None:
    # 2026-11-23: a 5 went past the ladder a 2 would have hit, and v6 said "fell short".
    # From 0 on the test board a 3 hits the 3->20 ladder.
    passed = play(5)
    facts = build_context(passed, passed["turns"][-1]["id"], V7).facts()
    missed = [item for item in facts["biggestSwings"] if item["kind"] == "missed_ladder"]
    assert missed and missed[0]["direction"] == "passed" and missed[0]["byPips"] == 2
    short = play(2)
    facts = build_context(short, short["turns"][-1]["id"], V7).facts()
    missed = [item for item in facts["biggestSwings"] if item["kind"] == "missed_ladder"]
    assert missed and missed[0]["direction"] == "short" and missed[0]["byPips"] == 1
    if facts["emotion"]["about"] == "missed_ladder":
        assert facts["emotion"]["direction"] == "short"


def test_v7_prompt_explains_passed_and_short() -> None:
    from commentator.services.llm.prompts import get_version

    text = get_version("commentary-v7").system_prompt(CommentarySettings(provider="claude"))
    assert "通り過ぎた" in text and "届かない" in text
