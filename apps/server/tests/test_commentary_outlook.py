"""Possibilities and the race given to the commentary model (llm/outlook.py)."""

from __future__ import annotations

from typing import Any

from commentator.domain.board import Board, Jump, JumpType
from commentator.domain.game import GameState, PlayerState
from commentator.services.llm.context import build_context, build_prompt
from commentator.services.llm.settings import CommentarySettings

# A small board: ladders 3->20 and 9->12, snakes 7->1 and 15->5, goal 30.
BOARD = Board("test", "Test", 30, (
    Jump(JumpType.LADDER, 3, 20),
    Jump(JumpType.LADDER, 9, 12),
    Jump(JumpType.SNAKE, 7, 1),
    Jump(JumpType.SNAKE, 15, 5),
))
SETTINGS = CommentarySettings(provider="claude")


def play(*rolls: int) -> dict[str, Any]:
    game = GameState.create(BOARD, [PlayerState("a", "あおい", "red"),
                                    PlayerState("b", "はると", "blue")])
    game.start()
    for value in rolls:
        game.roll(value)
    return game.to_dict()


def outlook(game: dict[str, Any]) -> dict[str, Any]:
    return build_context(game, game["turns"][-1]["id"], SETTINGS).outlook


def test_this_roll_lists_every_roll_and_marks_the_actual_one() -> None:
    result = outlook(play(1))  # あおい: 0 -> 1
    options = result["thisRollOptions"]
    assert [item["roll"] for item in options] == [1, 2, 3, 4, 5, 6]
    assert [item["actual"] for item in options] == [True, False, False, False, False, False]
    assert options[2] == {"roll": 3, "landsOn": 3, "final": 20, "jump": "ladder",
                          "actual": False}


def swings(result: dict[str, Any]) -> list[tuple[str, str, int, int]]:
    return [(item["kind"], item["player"], item["roll"], item["squares"])
            for item in result["biggestSwings"]]


def test_biggest_swings_are_sorted_by_squares_moved() -> None:
    # あおい 0 -> 1. This roll: 3 would have climbed 3->20. Next for あおい from 1:
    # 2 climbs 3->20, 6 hits 7->1. Next for はると from 0: 3 climbs 3->20.
    # Equal sizes: what this roll could have done comes before the next rolls.
    assert swings(outlook(play(1))) == [
        ("missed_ladder", "あおい", 3, 17),
        ("next_ladder", "あおい", 2, 17),
        ("next_ladder", "はると", 3, 17),
        ("next_snake", "あおい", 6, 6),
    ]


def test_a_hit_ladder_is_listed() -> None:
    assert swings(outlook(play(3)))[0] == ("hit_ladder", "あおい", 3, 17)


def test_a_hit_snake_ranks_by_size_and_the_list_is_cut_at_five() -> None:
    # あおい 5; はると 1; あおい 5 -> 7, snake to 1. From 5, a 4 would have climbed
    # 9->12 (3 squares) but five larger swings push it off the list.
    assert swings(outlook(play(5, 1, 2))) == [
        ("next_ladder", "あおい", 2, 17),
        ("next_ladder", "はると", 2, 17),
        ("hit_snake", "あおい", 2, 6),
        ("next_snake", "あおい", 6, 6),
        ("next_snake", "はると", 6, 6),
    ]


def test_next_rolls_for_both_players_and_goal_rolls() -> None:
    result = outlook(play(1))
    assert result["nextForPlayer"]["player"] == "あおい"
    assert result["nextForPlayer"]["position"] == 1
    assert result["nextForNextPlayer"]["player"] == "はると"
    assert result["nextForNextPlayer"]["ladderChances"] == [
        {"roll": 3, "from": 3, "to": 20, "squares": 17},
    ]
    near = outlook(play(3, 1, 6, 1, 2))  # あおい 3->20, 26, then 28
    assert near["nextForPlayer"]["goalRolls"] == [2]
    overshoot = [item for item in near["thisRollOptions"] if item.get("overshootStaysPut")]
    assert [item["roll"] for item in overshoot] == [5, 6]  # 26 + 5 > 30


def test_race_reports_a_lead_change_and_overtaking() -> None:
    result = outlook(play(2, 4, 3))  # あおい 2; はると 4; あおい 2 -> 5, passing はると
    race = result["race"]
    assert race["leaderBefore"] == "はると" and race["leaderAfter"] == "あおい"
    assert race["leadChanged"] is True
    assert race["overtook"] == ["はると"]
    assert race["leadMargin"] == 1
    assert result["gameFlow"]["leadChanges"] == 2
    assert result["gameFlow"]["leaderSinceTurn"] == 2


def test_a_snake_can_lose_the_lead() -> None:
    # あおい 6; はると 5; あおい 6 -> 7, snake to 1: はると takes the lead.
    race = outlook(play(6, 5, 1))["race"]
    assert race["leaderBefore"] == "あおい" and race["leaderAfter"] == "はると"
    assert race["leadChanged"] is True
    assert race["fellBehind"] == ["はると"]
    assert race["behindLeader"] == 4
    assert race["leadMargin"] is None

    # Already behind before the snake: nobody newly passes あおい.
    race = outlook(play(5, 6, 2))["race"]
    assert race["fellBehind"] == [] and race["behindLeader"] == 5


def test_biggest_jump_so_far_and_prompt_version() -> None:
    game = play(3, 1, 1)  # あおい climbs 3->20 in turn 1
    flow = outlook(game)["gameFlow"]
    assert flow["biggestJumpSoFar"] == {
        "player": "あおい", "turnNumber": 1, "jump": "ladder", "from": 3, "to": 20,
        "squares": 17,
    }
    prompt = build_prompt(build_context(game, game["turns"][-1]["id"], SETTINGS), SETTINGS)
    assert prompt.version == "commentary-v2"
    assert '"biggestSwings"' in prompt.user
    assert "biggestSwings" in prompt.system


def test_no_next_rolls_once_the_game_is_over() -> None:
    game = play(3, 1, 6, 1, 4)  # あおい 20 -> 26 -> 30: goal
    result = outlook(game)
    assert game["status"] == "completed"
    assert result["nextForPlayer"] is None and result["nextForNextPlayer"] is None
