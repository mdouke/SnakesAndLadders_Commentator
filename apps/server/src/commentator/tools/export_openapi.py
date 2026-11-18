"""Write the OpenAPI document that the web client's TypeScript types are generated from.

Usage: python -m commentator.tools.export_openapi [output]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..main import MemoryGameRepository, create_app
from ..presets import load_board_preset
from ..services.game import GameService
from ..services.recognition import RecognitionService

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "apps" / "web" / "openapi.json"


def build_openapi() -> dict[str, Any]:
    """Build the schema without touching the camera, settings file or database."""
    repository = MemoryGameRepository()
    recognition = RecognitionService(
        GameService(repository), pipeline=None, camera_provider=None
    )
    app = create_app(
        board=load_board_preset(), repository=repository, recognition_service=recognition
    )
    return app.openapi()


def render_openapi() -> str:
    return json.dumps(build_openapi(), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    output = Path(args[0]) if args else DEFAULT_OUTPUT
    output.write_text(render_openapi(), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
