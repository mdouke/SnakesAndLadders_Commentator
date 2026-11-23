"""Remake a played game's commentary with the current prompt and compare.

The game is replayed roll by roll from the database, each roll's comment is made
again with the current prompt and settings, and a Markdown report puts it next
to the comment that was shown during the game.

Usage (from the repository root):

    .venv/bin/python -m commentator.tools.compare_commentary            # newest game
    .venv/bin/python -m commentator.tools.compare_commentary --turns 14-22
    .venv/bin/python -m commentator.tools.compare_commentary --dry-run  # prompts only
    .venv/bin/python -m commentator.tools.compare_commentary --version commentary-v2

The report is written to ``var/prompt-comparisons/`` (not in Git: it holds
player names). Calling the model costs one request per roll; ``--dry-run``
writes the prompts without calling it.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.board import Board
from ..domain.game import GameState, PlayerState
from ..infrastructure.persistence.commentary_log import SQLiteCommentaryLog
from ..infrastructure.persistence.sqlite import SQLiteGameRepository
from ..infrastructure.secrets import default_secrets_path, load_secrets
from ..services.commentary import default_providers
from ..services.llm.context import CommentaryPrompt, build_context, build_prompt
from ..services.llm.prompts import get_version
from ..services.llm.providers import ProviderError, template_commentary
from ..services.llm.safety import review
from ..services.llm.settings import PROMPT_VERSIONS, CommentarySettingsStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
VAR = REPOSITORY_ROOT / "var"


def replay(game: dict[str, Any]) -> list[dict[str, Any]]:
    """The game state right after each roll, replayed from the recorded rolls.

    Only the rolls are replayed: position corrections and turn changes made by
    hand during the game are not.
    """
    state = GameState.create(
        Board.from_dict(game["board"]),
        [
            PlayerState(player["id"], player["name"], player["color"],
                        display_name=player.get("displayName"),
                        spoken_name=player.get("spokenName"))
            for player in game["players"]
        ],
        game_id=game["id"],
    )
    state.start()
    snapshots = []
    for turn in game["turns"]:
        state.roll(int(turn["roll"]))
        snapshots.append(state.to_dict())
    return snapshots


def shown_comments(log: SQLiteCommentaryLog, game_id: str) -> dict[str, dict[str, Any]]:
    """The last comment made for each turn during the game (by turn ID)."""
    shown: dict[str, dict[str, Any]] = {}
    for row in reversed(log.list(game_id=game_id, limit=10000)):  # oldest first
        if row.get("final_text") and row["status"] != "discarded":
            shown[row["turn_id"]] = row
    return shown


def newest_game_id(log: SQLiteCommentaryLog) -> str | None:
    rows = log.list(limit=1)
    return str(rows[0]["game_id"]) if rows else None


def parse_turns(value: str | None, count: int) -> range:
    if not value:
        return range(1, count + 1)
    first, _, last = value.partition("-")
    start = max(1, int(first))
    end = min(count, int(last or first))
    return range(start, end + 1)


def generate(prompt: CommentaryPrompt, provider: Any) -> tuple[str, str]:
    """(comment, note) made the way the server makes it, without retries."""
    settings = prompt.settings
    try:
        output = provider.generate(prompt, timeout=max(settings.timeout_seconds, 30.0))
    except ProviderError as error:
        return template_commentary(prompt.context, language=settings.language,
                                   honorific=settings.use_names), f"代替文（{error.code}）"
    checked = review(output.text, language=settings.language, maximum=prompt.max_length)
    if checked.text is None:
        return template_commentary(prompt.context, language=settings.language,
                                   honorific=settings.use_names), f"代替文（{checked.problem}）"
    return checked.text, output.model


def describe_turn(turn: dict[str, Any], name: str) -> str:
    move = f"{turn['before']}→{turn['normalPosition']}"
    if turn["jumpType"] == "ladder":
        move += f"、ハシゴで{turn['finalPosition']}"
    elif turn["jumpType"] == "snake":
        move += f"、ヘビで{turn['finalPosition']}"
    elif turn["overshoot"]:
        move = f"{turn['before']}のまま（ゴールを超える目）"
    return f"{name} 出目{turn['roll']}（{move}）"


def summarize_facts(facts: dict[str, Any]) -> str:
    race = facts.get("race") or {}
    chances = "、".join(
        f"{item['player']} {item['percentBefore']}→{item['percent']}%"
        for item in race.get("winChances", [])
    )
    swings = "、".join(
        f"{item['kind']}（{item['player']} {item['roll']}・{item['impact']}）"
        for item in (facts.get("biggestSwings") or [])[:3]
    )
    emotion = facts.get("emotion")
    feeling = (
        f"{emotion['kind']}・{emotion['strength'] or '—'}（{emotion['about']}）"
        if emotion else facts.get("reaction", "—")
    )
    parts = [
        f"反応 {feeling}",
        f"局面 {race.get('phaseBefore', '—')}→{race.get('phase', '—')}",
    ]
    if chances:
        parts.append(f"勝つ見込み {chances}")
    if swings:
        parts.append(f"上位の展開 {swings}")
    return "・".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game", help="game ID (default: the game commented most recently)")
    parser.add_argument("--turns", help="rolls to remake, e.g. 14-22 (1 = the first roll)")
    parser.add_argument("--dry-run", action="store_true", help="write prompts, call nothing")
    parser.add_argument("--db", type=Path, default=VAR / "commentator.sqlite3")
    parser.add_argument("--settings", type=Path, default=VAR / "commentary-settings.json")
    parser.add_argument("--output", type=Path,
                        help="report path (default: var/prompt-comparisons/)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--version", choices=PROMPT_VERSIONS,
                        help="prompt version to use (default: the one in the settings)")
    args = parser.parse_args(argv)

    load_secrets(default_secrets_path(REPOSITORY_ROOT))
    log = SQLiteCommentaryLog(args.db)
    game_id = args.game or newest_game_id(log)
    stored = SQLiteGameRepository(args.db).get(game_id) if game_id else None
    if stored is None:
        print("比較するゲームが見つかりません", file=sys.stderr)
        return 1
    game = stored.to_dict()
    settings = CommentarySettingsStore(args.settings).get()
    if args.version:
        settings = replace(settings, prompt_version=args.version)
    provider = default_providers().get(settings.provider)
    snapshots = replay(game)
    shown = shown_comments(log, game["id"])
    selected = parse_turns(args.turns, len(snapshots))
    names = {player["id"]: player["displayName"] for player in game["players"]}

    def make(prompt: CommentaryPrompt) -> tuple[str, str]:
        if args.dry_run or provider is None:
            return "（--dry-run のため作っていません）", "—"
        return generate(prompt, provider)

    def prompt_for(number: int, said: list[str]) -> CommentaryPrompt:
        snapshot = snapshots[number - 1]
        turn = snapshot["turns"][number - 1]
        return build_prompt(build_context(snapshot, turn["id"], settings, said), settings)

    prompts: list[tuple[int, dict[str, Any], CommentaryPrompt]] = []
    made: list[tuple[str, str]] = []
    if get_version(settings.prompt_version).previous_comments:
        # This version reads the comments said before, so they are made in order and
        # each new comment is what the next one sees. Rolls before the selected
        # range use the comments shown during the game.
        said = [
            shown[turn["id"]]["final_text"]
            for turn in game["turns"][: selected.start - 1] if turn["id"] in shown
        ]
        for number in selected:
            prompt = prompt_for(number, said)
            prompts.append((number, snapshots[number - 1], prompt))
            made.append(make(prompt))
            said.append(made[-1][0])
    else:
        prompts = [(number, snapshots[number - 1], prompt_for(number, []))
                   for number in selected]
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            made = list(pool.map(make, [prompt for _, _, prompt in prompts]))

    version = prompts[0][2].version if prompts else "—"
    lines = [
        f"# 実況の比較: {' 対 '.join(names.values())}",
        "",
        f"- ゲーム: `{game['id']}`（{len(snapshots)}回の出目、{game['status']}）",
        f"- 作成: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 新しい実況: `{version}`・{settings.provider}（{settings.model}）・"
        f"{settings.length}・{settings.style}",
        "- 出目だけを再現している（手で行った位置の訂正・手番の変更は含まない）。",
        "",
    ]
    for (number, snapshot, prompt), (text, note) in zip(prompts, made, strict=True):
        turn = snapshot["turns"][number - 1]
        original = game["turns"][number - 1]
        old = shown.get(original["id"])
        facts = prompt.context.facts()
        lines += [
            f"## {number}. ターン{turn['round']}・{describe_turn(turn, names[turn['playerId']])}",
            "",
            f"**前（{old['prompt_version'] if old else '—'}）** "
            f"{old['final_text'] if old else '（記録なし）'}",
            "",
            f"**新（{version}・{note}）** {text}",
            "",
            f"<sub>{summarize_facts(facts)}</sub>",
            "",
            "<details><summary>ユーザープロンプト</summary>",
            "",
            "```json",
            prompt.user.split("\n", 1)[1] if "\n" in prompt.user else prompt.user,
            "```",
            "",
            "</details>",
            "",
        ]
    output = args.output or (
        VAR / "prompt-comparisons"
        / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{game['id'][:8]}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
