"""Saved sample games (data/sample-games/) and the v8 grouping they showed."""

from __future__ import annotations

from typing import Any

from commentator.services.llm.context import build_context
from commentator.services.llm.outlook_v8 import group_swings
from commentator.services.llm.settings import CommentarySettings
from commentator.tools.compare_commentary import replay
from commentator.tools.save_sample_game import build_sample, load_sample

V7 = CommentarySettings(provider="claude", prompt_version="commentary-v7")
V8 = CommentarySettings(provider="claude", prompt_version="commentary-v8")


def test_sample_replays_to_the_saved_result() -> None:
    sample = load_sample("seesaw-to-99")
    game = sample["game"]
    snapshots = replay(game)
    assert len(snapshots) == len(game["turns"]) == 74
    final = {player["displayName"]: player["position"] for player in snapshots[-1]["players"]}
    assert final == {"あお": 99, "おれんじ": 100}
    assert len(sample["shownComments"]) == 74


def _facts(number: int, settings: CommentarySettings) -> dict[str, Any]:
    snapshot = replay(load_sample("seesaw-to-99")["game"])[number - 1]
    return build_context(snapshot, snapshot["turns"][number - 1]["id"], settings).facts()


def test_v8_groups_the_48_and_49_snakes() -> None:
    # Roll 51 (round 26): あお 44 -> 46. A 4 would have hit the 48 snake and a 5 the 49 snake;
    # next turn a 2 or a 3 does. v7 sent four items and only "49" was ever told.
    v7 = [(item["kind"], item["from"]) for item in _facts(51, V7)["biggestSwings"]]
    assert ("avoided_snake", 48) in v7 and ("avoided_snake", 49) in v7
    swings = _facts(51, V8)["biggestSwings"]
    avoided = [item for item in swings if item["kind"] == "avoided_snake"]
    assert len(avoided) == 1
    assert {avoided[0]["from"], *(other["from"] for other in avoided[0]["alsoWith"])} == {48, 49}
    following = [item for item in swings if item["kind"] == "next_snake"]
    assert len(following) == 1 and following[0]["alsoWith"]


def test_group_swings_keeps_order_and_other_kinds() -> None:
    swings = [
        {"kind": "next_ladder", "player": "a", "roll": 5, "from": 51, "to": 67},
        {"kind": "next_snake", "player": "a", "roll": 3, "from": 49, "to": 11},
        {"kind": "next_snake", "player": "b", "roll": 1, "from": 49, "to": 11},
        {"kind": "next_snake", "player": "a", "roll": 2, "from": 48, "to": 26},
        {"kind": "hit_ladder", "player": "a", "roll": 6, "from": 3, "to": 20},
    ]
    grouped = group_swings(swings)
    assert [(item["kind"], item["player"]) for item in grouped] == [
        ("next_ladder", "a"), ("next_snake", "a"), ("next_snake", "b"), ("hit_ladder", "a"),
    ]
    assert grouped[1]["alsoWith"] == [{"roll": 2, "from": 48, "to": 26}]
    assert "alsoWith" not in swings[1]  # the input is not changed


def test_build_sample_keeps_rolls_and_comments_and_can_rename() -> None:
    game = {
        "id": "g", "board": {"id": "standard"}, "status": "completed",
        "players": [{"id": "p", "name": "Alice", "displayName": "Alice", "color": "red"}],
        "turns": [{"id": "t1", "roll": 3}, {"id": "t2", "roll": 4}],
        "undoHistory": ["not saved"],
    }
    shown = {"t1": {"promptVersion": "commentary-v8", "model": "m", "text": "Aliceさん、3です"}}
    sample = build_sample(game, shown, name="x", note="n", renames={"Alice": "あか"})
    assert "undoHistory" not in sample["game"]
    assert sample["game"]["players"][0]["name"] == "あか"
    assert sample["shownComments"] == [
        {"turnId": "t1", "promptVersion": "commentary-v8", "model": "m", "text": "あかさん、3です"}
    ]
