"""Save a played game as a sample, to remake its commentary with any prompt later.

The file keeps the board, the players, every roll and the comment shown for
each roll, so ``compare_commentary --sample NAME`` can replay it even after the
database was cleaned up. Undo history and operation events are left out.

Usage (from the repository root):

    .venv/bin/python -m commentator.tools.save_sample_game --name close-race     # newest game
    .venv/bin/python -m commentator.tools.save_sample_game --game ID --name close-race \\
        --note "Lead changes four times; both players dodge the 48/49 snakes"

Samples are written to ``data/sample-games/NAME.json`` and are meant to be kept
in Git. Player names are saved as played: use names that are not personal
(colours, for example), or rename them with ``--names``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..infrastructure.persistence.commentary_log import SQLiteCommentaryLog
from ..infrastructure.persistence.sqlite import SQLiteGameRepository

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
VAR = REPOSITORY_ROOT / "var"
SAMPLES = REPOSITORY_ROOT / "data" / "sample-games"

# Only what a replay needs; the rest (undo history, events) is not saved.
GAME_KEYS = ("id", "board", "players", "status", "turns", "startedAt", "endedAt")


def sample_path(name: str) -> Path:
    """``NAME`` in data/sample-games/, or a path to a JSON file."""
    path = Path(name)
    if path.suffix == ".json":
        return path
    return SAMPLES / f"{name}.json"


def load_sample(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(sample_path(name).read_text(encoding="utf-8"))
    return loaded


def shown_comments(log: SQLiteCommentaryLog, game_id: str) -> dict[str, dict[str, Any]]:
    """The last comment shown for each turn (by turn ID), oldest request first."""
    shown: dict[str, dict[str, Any]] = {}
    for row in reversed(log.list(game_id=game_id, limit=10000)):
        if row.get("final_text") and row["status"] != "discarded":
            shown[row["turn_id"]] = {
                "promptVersion": row["prompt_version"],
                "model": row.get("model"),
                "text": row["final_text"],
            }
    return shown


def build_sample(
    game: dict[str, Any], shown: dict[str, dict[str, Any]], *, name: str, note: str,
    renames: dict[str, str] | None = None,
) -> dict[str, Any]:
    kept = {key: game[key] for key in GAME_KEYS if key in game}
    renames = renames or {}
    if renames:
        kept["players"] = [
            {**player, **{
                key: renames.get(str(player.get(key)), player.get(key))
                for key in ("name", "displayName", "spokenName") if player.get(key)
            }}
            for player in kept["players"]
        ]
    comments = []
    for turn in kept["turns"]:
        item = shown.get(turn["id"])
        if item is None:
            continue
        text = item["text"]
        for old, new in renames.items():
            text = text.replace(old, new)
        comments.append({"turnId": turn["id"], **item, "text": text})
    return {
        "name": name,
        "note": note,
        "savedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "sourceGameId": game["id"],
        "game": kept,
        "shownComments": comments,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="file name in data/sample-games/")
    parser.add_argument("--game", help="game ID (default: the game updated most recently)")
    parser.add_argument("--note", default="", help="why this game is a good sample")
    parser.add_argument("--names", default="",
                        help="rename players, e.g. 'Alice=あお,Bob=おれんじ'")
    parser.add_argument("--db", type=Path, default=VAR / "commentator.sqlite3")
    parser.add_argument("--force", action="store_true", help="overwrite an existing sample")
    args = parser.parse_args(argv)

    repository = SQLiteGameRepository(args.db)
    game_id = args.game or newest_game_id(args.db)
    stored = repository.get(game_id) if game_id else None
    if stored is None:
        print("ゲームが見つかりません", file=sys.stderr)
        return 1
    path = sample_path(args.name)
    if path.exists() and not args.force:
        print(f"{path} は既にあります（上書きするなら --force）", file=sys.stderr)
        return 1
    renames = dict(
        pair.split("=", 1) for pair in args.names.split(",") if "=" in pair
    )
    sample = build_sample(
        stored.to_dict(), shown_comments(SQLiteCommentaryLog(args.db), stored.id),
        name=args.name, note=args.note, renames=renames,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sample, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    rolls, comments = len(sample["game"]["turns"]), len(sample["shownComments"])
    print(f"{path}（{rolls}回の出目、実況 {comments}件）")
    return 0


def newest_game_id(db: Path) -> str | None:
    import sqlite3

    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT id FROM games ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return str(row[0]) if row else None


if __name__ == "__main__":
    raise SystemExit(main())
