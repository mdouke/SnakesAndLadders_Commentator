"""Load versioned board presets from the repository data directory."""

from __future__ import annotations

import json
from pathlib import Path

from .domain.board import Board


def repository_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "data" / "boards").is_dir():
            return candidate
    raise RuntimeError("リポジトリのdata/boardsディレクトリが見つかりません")


def load_board_preset(name: str = "standard") -> Board:
    path = repository_root() / "data" / "boards" / f"{name}.json"
    with path.open(encoding="utf-8") as file:
        return Board.from_dict(json.load(file))

