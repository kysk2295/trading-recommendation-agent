from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import trading_agent.bar_archive as archive


def test_candidate_input_snapshot_is_append_only_per_observation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "paper.sqlite3"
    observed_at = dt.datetime(
        2026,
        7,
        14,
        10,
        5,
        30,
        tzinfo=ZoneInfo("America/New_York"),
    )
    snapshot = archive.CandidateInputSnapshot(
        "NAS",
        "DEMO",
        observed_at,
        observed_at.replace(second=0) - dt.timedelta(minutes=1),
        10.0,
        250_000,
        25.0,
    )

    first = archive.archive_candidate_input(database, snapshot)
    duplicate = archive.archive_candidate_input(database, snapshot)

    with sqlite3.connect(database) as connection:
        rows = tuple(
            connection.execute(
                "SELECT exchange, symbol, observed_at, latest_completed_bar_at, "
                "prior_close, average_daily_volume, spread_bps "
                "FROM candidate_input_snapshots"
            ).fetchall()
        )
    assert first == 1
    assert duplicate == 0
    assert rows == (
        (
            "NAS",
            "DEMO",
            observed_at.isoformat(),
            (observed_at.replace(second=0) - dt.timedelta(minutes=1)).isoformat(),
            10.0,
            250_000,
            25.0,
        ),
    )
