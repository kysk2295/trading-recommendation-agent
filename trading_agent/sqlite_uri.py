from __future__ import annotations

from pathlib import Path

from trading_agent.private_directory_identity import absolute_private_path


def sqlite_read_only_uri(path: Path) -> str:
    return f"{absolute_private_path(path).as_uri()}?mode=ro"
