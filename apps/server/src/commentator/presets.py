"""Board presets: built-in boards in ``data/boards`` and user boards in ``var/boards``."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain.board import Board

BOARD_GOAL = 100
_PRESET_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def repository_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "data" / "boards").is_dir():
            return candidate
    raise RuntimeError("リポジトリのdata/boardsディレクトリが見つかりません")


def load_board_preset(name: str = "standard") -> Board:
    path = repository_root() / "data" / "boards" / f"{name}.json"
    with path.open(encoding="utf-8") as file:
        return Board.from_dict(json.load(file))


class BoardPresetError(ValueError):
    """A preset that cannot be found, parsed, validated or saved."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BoardPresetSummary:
    id: str
    name: str
    built_in: bool
    snake_count: int
    ladder_count: int


def board_from_definition(definition: dict[str, Any]) -> Board:
    """Build and validate a board from editor input.

    Raises ``BoardPresetError`` with code ``invalid_board`` and the domain's message.
    """
    try:
        board = Board.from_dict(definition)
    except (KeyError, TypeError, ValueError) as error:
        message = str(error) if isinstance(error, ValueError) else "盤面の形式が正しくありません"
        raise BoardPresetError("invalid_board", message) from error
    if board.goal != BOARD_GOAL:
        raise BoardPresetError("invalid_board", f"盤面は1〜{BOARD_GOAL}マスにしてください")
    if not board.name.strip():
        raise BoardPresetError("invalid_board", "盤面の名前を入力してください")
    return board


class BoardPresetStore:
    def __init__(self, built_in_dir: Path, user_dir: Path) -> None:
        self.built_in_dir = built_in_dir
        self.user_dir = user_dir

    @classmethod
    def default(cls) -> BoardPresetStore:
        root = repository_root()
        user_dir = Path(os.environ.get("COMMENTATOR_BOARD_DIR", str(root / "var" / "boards")))
        return cls(root / "data" / "boards", user_dir)

    def list(self) -> list[BoardPresetSummary]:
        summaries: dict[str, BoardPresetSummary] = {}
        for built_in, directory in ((False, self.user_dir), (True, self.built_in_dir)):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                try:
                    board = self._read(path)
                except BoardPresetError:
                    continue  # a broken user file must not hide the others
                summaries[board.id] = BoardPresetSummary(
                    id=board.id,
                    name=board.name,
                    built_in=built_in,
                    snake_count=sum(jump.type.value == "snake" for jump in board.jumps),
                    ladder_count=sum(jump.type.value == "ladder" for jump in board.jumps),
                )
        return sorted(summaries.values(), key=lambda item: (not item.built_in, item.name))

    def get(self, preset_id: str) -> Board:
        self._check_id(preset_id)
        for directory in (self.built_in_dir, self.user_dir):
            path = directory / f"{preset_id}.json"
            if path.is_file():
                return self._read(path)
        raise BoardPresetError("board_not_found", "盤面が見つかりません")

    def is_built_in(self, preset_id: str) -> bool:
        return (self.built_in_dir / f"{preset_id}.json").is_file()

    def save(self, definition: dict[str, Any]) -> Board:
        board = board_from_definition(definition)
        self._check_id(board.id)
        if self.is_built_in(board.id):
            raise BoardPresetError(
                "board_preset_read_only",
                "標準の盤面は上書きできません。別のIDで保存してください",
            )
        self.user_dir.mkdir(parents=True, exist_ok=True)
        payload = {"schemaVersion": 1, **board.to_dict()}
        target = self.user_dir / f"{board.id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(target)
        return board

    def delete(self, preset_id: str) -> None:
        self._check_id(preset_id)
        if self.is_built_in(preset_id):
            raise BoardPresetError("board_preset_read_only", "標準の盤面は削除できません")
        path = self.user_dir / f"{preset_id}.json"
        if not path.is_file():
            raise BoardPresetError("board_not_found", "盤面が見つかりません")
        path.unlink()

    @staticmethod
    def _check_id(preset_id: str) -> None:
        if not _PRESET_ID.match(preset_id):
            raise BoardPresetError(
                "invalid_board",
                "盤面IDは半角英小文字・数字・ハイフンで、40文字以内にしてください",
            )

    @staticmethod
    def _read(path: Path) -> Board:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BoardPresetError(
                "invalid_board", f"盤面ファイルを読めません: {path.name}"
            ) from error
        board = board_from_definition(data)
        if board.id != path.stem:
            raise BoardPresetError(
                "invalid_board", f"盤面IDとファイル名が一致しません: {path.name}"
            )
        return board
