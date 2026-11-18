"""Load API keys from a local ``KEY=VALUE`` file into the process environment.

The file (default ``var/secrets.env``, ignored by Git) is read once at startup.
Variables already set in the environment win, so ``export`` in the terminal still
overrides the file. Values are never logged; only the variable names are.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from collections.abc import MutableMapping
from pathlib import Path

SECRETS_PATH_ENV = "COMMENTATOR_SECRETS_PATH"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines; ``#`` comments, ``export`` and quotes are allowed."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not _NAME.match(name):
            raise ValueError(f"{number}行目を読めません（KEY=VALUE の形で書いてください）")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def load_secrets(
    path: Path, environ: MutableMapping[str, str] | None = None
) -> list[str]:
    """Copy variables from ``path`` into ``environ``; return the names that were set."""
    environment = os.environ if environ is None else environ
    logger = logging.getLogger("commentator.secrets")
    if not path.exists():
        return []
    try:
        mode = path.stat().st_mode
        values = parse_env_file(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        logger.error(
            "secrets file unreadable",
            extra={"event": "secrets_unreadable", "errorCode": "secrets_unreadable",
                   "path": str(path), "reason": str(error)},
        )
        return []
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            "secrets file is readable by other users",
            extra={"event": "secrets_permissions", "errorCode": "secrets_permissions",
                   "path": str(path), "hint": f"chmod 600 {path}"},
        )
    loaded = []
    for name, value in values.items():
        if not value or name in environment:
            continue  # an explicit environment variable wins over the file
        environment[name] = value
        loaded.append(name)
    logger.info(
        "secrets loaded",
        extra={"event": "secrets_loaded", "path": str(path), "names": sorted(loaded)},
    )
    return loaded


def default_secrets_path(repository_root: Path) -> Path:
    configured = os.environ.get(SECRETS_PATH_ENV)
    return Path(configured) if configured else repository_root / "var" / "secrets.env"
