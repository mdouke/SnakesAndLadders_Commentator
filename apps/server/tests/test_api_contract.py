from __future__ import annotations

import json

from commentator.tools.export_openapi import DEFAULT_OUTPUT, render_openapi


def test_committed_openapi_matches_the_app() -> None:
    committed = DEFAULT_OUTPUT.read_text(encoding="utf-8")
    assert committed == render_openapi(), (
        "apps/web/openapi.json is stale. Run "
        "`.venv/bin/python -m commentator.tools.export_openapi` and "
        "`npm run generate:api`, then commit both files."
    )


def test_openapi_publishes_the_realtime_message_union() -> None:
    schemas = json.loads(render_openapi())["components"]["schemas"]
    assert schemas["RealtimeMessage"]["discriminator"]["propertyName"] == "type"
    assert set(schemas["RealtimeMessage"]["discriminator"]["mapping"]) == {
        "game", "recognition", "commentary",
    }
