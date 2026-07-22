from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final, override

WATCH_DATABASE_NAME: Final = "paper_recommendations.sqlite3"
ORB_STRATEGY: Final = "opening_range_breakout"


class InvalidUsDayNoSetupSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US Day no-setup source is invalid"


def require_no_orb_recommendation(
    repository: Path,
    source_artifacts: tuple[Path, ...],
    session_bounds: tuple[dt.datetime, dt.datetime],
) -> None:
    databases = tuple(path for path in source_artifacts if path.name == WATCH_DATABASE_NAME)
    if len(databases) != 1:
        raise InvalidUsDayNoSetupSourceError
    path = repository / databases[0]
    try:
        with closing(sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True)) as connection:
            _ = connection.execute("PRAGMA query_only = ON")
            rows: list[tuple[str]] = connection.execute(
                "SELECT created_at FROM recommendations WHERE strategy = ? ORDER BY created_at",
                (ORB_STRATEGY,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        raise InvalidUsDayNoSetupSourceError from None
    if any(_is_in_session(row[0], session_bounds) for row in rows):
        raise InvalidUsDayNoSetupSourceError


def _is_in_session(raw: str, session_bounds: tuple[dt.datetime, dt.datetime]) -> bool:
    try:
        observed_at = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise InvalidUsDayNoSetupSourceError from None
    if observed_at.tzinfo is None:
        raise InvalidUsDayNoSetupSourceError
    return session_bounds[0] <= observed_at < session_bounds[1]
