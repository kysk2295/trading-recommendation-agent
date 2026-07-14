from __future__ import annotations

import csv
import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.bar_archive import CREATE_CANDIDATE_BARS, CREATE_CANDIDATE_INPUTS
from trading_agent.market_risk import MARKET_RISK_HEADER
from trading_agent.metrics import extract_paper_trades
from trading_agent.metrics_report import write_metrics_report
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.opening_gap import SNAPSHOT_HEADER
from trading_agent.store import PaperStore


def write_complete_session(
    session: Path,
    session_date: dt.date = dt.date(2026, 7, 14),
) -> None:
    session.mkdir(parents=True)
    database = session / "paper_recommendations.sqlite3"
    store = PaperStore(database)
    created_at = dt.datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        10,
        0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    store.save(
        Recommendation(
            "orb-one",
            "DEMO",
            "opening_range_breakout",
            created_at,
            10.0,
            9.5,
            10.5,
            11.0,
            RecommendationState.SETUP,
            "fixture",
        )
    )
    store.set_state(
        "orb-one",
        RecommendationState.ACTIVE,
        created_at + dt.timedelta(minutes=1),
        10.0,
        "조건부 진입가 도달",
    )
    store.set_state(
        "orb-one",
        RecommendationState.TARGET_2R,
        created_at + dt.timedelta(minutes=5),
        11.0,
        "2R 목표가 도달",
    )
    with sqlite3.connect(database) as connection:
        _ = connection.execute(CREATE_CANDIDATE_BARS)
        _ = connection.execute(
            "INSERT INTO candidate_minute_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "NAS",
                "DEMO",
                (created_at - dt.timedelta(minutes=1)).isoformat(),
                created_at.isoformat(),
                created_at.isoformat(),
                9.9,
                10.1,
                9.8,
                10.0,
                100_000,
                1_000_000,
            ),
        )
        _ = connection.execute(CREATE_CANDIDATE_INPUTS)
        _ = connection.execute(
            "INSERT INTO candidate_input_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "NAS",
                "DEMO",
                created_at.isoformat(),
                (created_at - dt.timedelta(minutes=1)).isoformat(),
                9.4,
                1_000_000,
                25.0,
            ),
        )
    write_metrics_report(session / "paper_metrics", extract_paper_trades((store,)))
    observed_at = "2026-07-14T10:00:00-04:00"
    with (session / "kis_ranking_request_coverage.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "observed_at",
                "ranking_source",
                "exchange",
                "status",
                "row_count",
                "reason",
            )
        )
        writer.writerows(
            (observed_at, source, exchange, "ok", 100, "")
            for exchange in ("NAS", "NYS", "AMS")
            for source in ("updown", "volume")
        )
    (session / "watch_cycles.csv").write_text(
        "started_at,exit_code,status\n2026-07-14T10:00:00-04:00,0,ok\n",
        encoding="utf-8",
    )
    (session / "kis_read_retry_cycles.csv").write_text(
        "started_at,retry_count,recovered_count,repeated_failure_count\n2026-07-14T10:00:00-04:00,1,1,0\n",
        encoding="utf-8",
    )
    (session / "kis_read_retry_events.csv").write_text(
        "started_at,endpoint,exchange,symbol,first_status,final_status,outcome\n"
        "2026-07-14T10:00:00-04:00,/minute,NAS,DEMO,500,200,recovered\n",
        encoding="utf-8",
    )
    (session / "candidate_input_cycles.csv").write_text(
        "started_at,selected_count,context_count,scan_completed\n2026-07-14T10:00:00-04:00,1,1,True\n",
        encoding="utf-8",
    )
    (session / "kis_ranking_snapshots.csv").write_text(
        "observed_at,symbol\n2026-07-14T10:00:00-04:00,DEMO\n",
        encoding="utf-8",
    )
    with (session / "market_risk_screen.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKET_RISK_HEADER)
        writer.writerow(
            (
                (created_at - dt.timedelta(minutes=1)).isoformat(),
                "NAS",
                "DEMO",
                True,
                "",
                0.08,
                10.0,
                9.99,
                10.01,
                25.0,
                65.0,
                2_000_000.0,
                300_000,
                1_000_000,
                0.30,
            )
        )
    with (session / "kis_opening_gap_snapshots.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SNAPSHOT_HEADER)
        writer.writerow(
            (
                (created_at - dt.timedelta(minutes=2)).isoformat(),
                (created_at - dt.timedelta(minutes=1, seconds=30)).isoformat(),
                "NAS",
                "DEMO",
                "ok",
                9.4,
                10.0,
                10.0 / 9.4 - 1.0,
                10.0,
                300_000,
                1_000_000,
                "",
            )
        )
