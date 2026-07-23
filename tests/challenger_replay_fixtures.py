from __future__ import annotations

import csv
import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.bar_archive import CREATE_CANDIDATE_BARS, CREATE_CANDIDATE_INPUTS
from trading_agent.store import PaperStore

NEW_YORK = ZoneInfo("America/New_York")
SqlValue = str | float | int


def write_closed_source_session(
    session: Path,
    *,
    include_censored_symbol: bool = True,
    post_session_complete: bool = True,
    session_date: dt.date = dt.date(2026, 7, 14),
) -> None:
    session.mkdir(parents=True)
    database = session / "paper_recommendations.sqlite3"
    _ = PaperStore(database)
    observed_at = dt.datetime.combine(
        session_date,
        dt.time(9, 35, 30),
        tzinfo=NEW_YORK,
    )
    contexts = (("NAS", "DEMO"),)
    if include_censored_symbol:
        contexts = (*contexts, ("NYS", "SHORT"))
    with sqlite3.connect(database) as connection:
        _ = connection.execute(CREATE_CANDIDATE_BARS)
        _ = connection.execute(CREATE_CANDIDATE_INPUTS)
        _ = connection.executemany(
            "INSERT INTO candidate_input_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    exchange,
                    symbol,
                    observed_at.isoformat(),
                    (observed_at - dt.timedelta(minutes=1, seconds=30)).isoformat(),
                    10.0,
                    100_000,
                    20.0,
                )
                for exchange, symbol in contexts
            ),
        )
        _ = connection.executemany(
            "INSERT INTO candidate_minute_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _complete_gap_bars(observed_at),
        )
        if include_censored_symbol:
            _ = connection.executemany(
                "INSERT INTO candidate_minute_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _short_bars(observed_at),
            )
    _write_quality_files(session, observed_at, len(contexts))
    if post_session_complete:
        (session / "post_session_metrics_cycles.csv").write_text(
            "started_at,exit_code,status\n"
            + f"{dt.datetime.combine(session_date, dt.time(16, 1, 31), tzinfo=NEW_YORK).isoformat()},0,ok\n",
            encoding="utf-8",
        )


def _complete_gap_bars(observed_at: dt.datetime) -> tuple[tuple[SqlValue, ...], ...]:
    opened_at = dt.datetime.combine(
        observed_at.date(),
        dt.time(9, 30),
        tzinfo=NEW_YORK,
    )
    rows: list[tuple[SqlValue, ...]] = []
    for index in range(390):
        timestamp = opened_at + dt.timedelta(minutes=index)
        first_observed_at = observed_at if index < 5 else timestamp + dt.timedelta(minutes=1, seconds=30)
        close = 10.72 + index * 0.04 if index < 5 else 10.90
        open_price = 10.70 if index == 0 else close - 0.03
        rows.append(
            (
                "NAS",
                "DEMO",
                timestamp.isoformat(),
                first_observed_at.isoformat(),
                timestamp.astimezone(ZoneInfo("Asia/Seoul")).isoformat(),
                open_price,
                max(close + 0.05, 10.95 if index == 6 else close),
                min(open_price - 0.02, close - 0.02),
                close,
                10_000 if index < 5 else 1_000,
                100_000,
            )
        )
    return tuple(rows)


def _short_bars(observed_at: dt.datetime) -> tuple[tuple[SqlValue, ...], ...]:
    opened_at = dt.datetime.combine(
        observed_at.date(),
        dt.time(9, 30),
        tzinfo=NEW_YORK,
    )
    return tuple(
        (
            "NYS",
            "SHORT",
            (opened_at + dt.timedelta(minutes=index)).isoformat(),
            observed_at.isoformat(),
            (opened_at + dt.timedelta(minutes=index)).astimezone(ZoneInfo("Asia/Seoul")).isoformat(),
            10.5,
            10.7,
            10.4,
            10.6,
            10_000,
            100_000,
        )
        for index in range(5)
    )


def _write_quality_files(session: Path, observed_at: dt.datetime, context_count: int) -> None:
    with (session / "kis_ranking_request_coverage.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("observed_at", "ranking_source", "exchange", "status", "row_count", "reason"))
        writer.writerows(
            (observed_at.isoformat(), source, exchange, "ok", 100, "")
            for exchange in ("NAS", "NYS", "AMS")
            for source in ("updown", "volume")
        )
    (session / "watch_cycles.csv").write_text(
        f"started_at,exit_code,status\n{observed_at.isoformat()},0,ok\n",
        encoding="utf-8",
    )
    (session / "kis_read_retry_cycles.csv").write_text(
        f"started_at,retry_count,recovered_count,repeated_failure_count\n{observed_at.isoformat()},0,0,0\n",
        encoding="utf-8",
    )
    (session / "candidate_input_cycles.csv").write_text(
        "started_at,selected_count,context_count,scan_completed\n"
        + f"{observed_at.isoformat()},{context_count},{context_count},True\n",
        encoding="utf-8",
    )
