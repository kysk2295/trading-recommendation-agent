from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

WATCH_DATABASE_NAME: Final = "paper_recommendations.sqlite3"
ORB_STRATEGY: Final = "opening_range_breakout"


class InvalidUsDayNoSetupSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "US Day no-setup source is invalid"


@dataclass(frozen=True, slots=True)
class OrbSessionRecommendation:
    recommendation_id: str
    symbol: str
    created_at: dt.datetime


def load_session_orb_recommendations(
    repository: Path,
    source_artifacts: tuple[Path, ...],
    session_bounds: tuple[dt.datetime, dt.datetime],
) -> tuple[OrbSessionRecommendation, ...]:
    databases = tuple(path for path in source_artifacts if path.name == WATCH_DATABASE_NAME)
    if len(databases) != 1:
        raise InvalidUsDayNoSetupSourceError
    path = repository / databases[0]
    try:
        with closing(sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True)) as connection:
            _ = connection.execute("PRAGMA query_only = ON")
            rows: list[tuple[str, str, str]] = connection.execute(
                "SELECT recommendation_id, symbol, created_at FROM recommendations "
                "WHERE strategy = ? ORDER BY created_at, symbol, recommendation_id",
                (ORB_STRATEGY,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        raise InvalidUsDayNoSetupSourceError from None
    recommendations = tuple(_recommendation(row) for row in rows)
    return tuple(item for item in recommendations if session_bounds[0] <= item.created_at < session_bounds[1])


def _recommendation(row: tuple[str, str, str]) -> OrbSessionRecommendation:
    try:
        created_at = dt.datetime.fromisoformat(row[2])
    except ValueError:
        raise InvalidUsDayNoSetupSourceError from None
    if not row[0] or not row[1] or created_at.tzinfo is None:
        raise InvalidUsDayNoSetupSourceError
    return OrbSessionRecommendation(row[0], row[1], created_at)
