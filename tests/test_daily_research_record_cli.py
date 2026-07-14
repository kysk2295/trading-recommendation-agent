from __future__ import annotations

import csv
import datetime as dt
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TypedDict
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from trading_agent.metrics import extract_paper_trades
from trading_agent.metrics_report import write_metrics_report
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.store import PaperStore


class QualityJson(TypedDict):
    forward_day_eligible: bool
    completed_trades: int


class MetricsJson(TypedDict):
    side_cost_bps: int
    trade_count: int


class PromotionJson(TypedDict):
    allowed: bool
    cumulative_forward_days: int
    blockers: list[str]


class DailyRecordJson(TypedDict):
    session_date: str
    code_version: str
    strategy_stage: str
    session_quality: QualityJson
    metrics_20bp: MetricsJson
    promotion: PromotionJson


RECORD_ADAPTER = TypeAdapter(DailyRecordJson)


def test_daily_research_cli_writes_lineage_and_blocks_early_promotion(
    tmp_path: Path,
) -> None:
    # Given: one complete ORB shadow trade and one fully covered provider cycle.
    session = tmp_path / "live_sessions" / "20260714"
    _write_complete_session(session)
    project = Path(__file__).parents[1]
    script = project / "run_daily_research_record.py"
    assert script.is_file(), "daily research CLI is missing"

    # When: the closed session is recorded with an explicit code version.
    completed = subprocess.run(
        (
            sys.executable,
            str(script),
            str(session),
            "--session-date",
            "2026-07-14",
            "--strategy",
            "orb",
            "--code-version",
            "test-code",
        ),
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: immutable lineage exists but the 60-day/100-trade gate blocks promotion.
    assert completed.returncode == 0, completed.stderr
    ledger = session.parent / "daily_research_ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = RECORD_ADAPTER.validate_json(lines[0])
    assert record["session_date"] == "2026-07-14"
    assert record["code_version"] == "test-code"
    assert record["strategy_stage"] == "experimental_shadow"
    assert record["session_quality"]["forward_day_eligible"] is True
    assert record["session_quality"]["completed_trades"] == 1
    assert record["metrics_20bp"]["side_cost_bps"] == 20
    assert record["metrics_20bp"]["trade_count"] == 1
    assert record["promotion"]["allowed"] is False
    assert "minimum_forward_days:1/60" in record["promotion"]["blockers"]
    assert "minimum_completed_trades:1/100" in record["promotion"]["blockers"]
    summary = (session / "daily_research_summary_ko.md").read_text(encoding="utf-8")
    assert "승격 금지" in summary
    assert "확정 수익" in summary


def test_rerunning_older_session_does_not_use_future_ledger_rows(
    tmp_path: Path,
) -> None:
    # Given: two eligible sessions were recorded in chronological order.
    sessions = tmp_path / "live_sessions"
    first = sessions / "20260714"
    second = sessions / "20260715"
    _write_complete_session(first, dt.date(2026, 7, 14))
    _write_complete_session(second, dt.date(2026, 7, 15))
    project = Path(__file__).parents[1]
    script = project / "run_daily_research_record.py"
    for session, session_date in (
        (first, "2026-07-14"),
        (second, "2026-07-15"),
        (first, "2026-07-14"),
    ):
        completed = subprocess.run(
            (
                sys.executable,
                str(script),
                str(session),
                "--session-date",
                session_date,
                "--strategy",
                "orb",
                "--code-version",
                "test-code",
            ),
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    # Then: the older replay is idempotent and retains its original as-of total.
    lines = (sessions / "daily_research_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = tuple(RECORD_ADAPTER.validate_json(line) for line in lines)
    first_record = next(row for row in records if row["session_date"] == "2026-07-14")
    assert first_record["promotion"]["cumulative_forward_days"] == 1


def _write_complete_session(
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
        _ = connection.execute("CREATE TABLE candidate_minute_bars (value INTEGER)")
        _ = connection.execute("INSERT INTO candidate_minute_bars VALUES (1)")
    write_metrics_report(
        session / "paper_metrics",
        extract_paper_trades((store,)),
    )
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
        "started_at,exit_code,status\n" + "2026-07-14T10:00:00-04:00,0,ok\n",
        encoding="utf-8",
    )
    (session / "kis_ranking_snapshots.csv").write_text(
        "observed_at,symbol\n2026-07-14T10:00:00-04:00,DEMO\n",
        encoding="utf-8",
    )
    (session / "market_risk_screen.csv").write_text(
        "observed_at,symbol,selected\n2026-07-14T10:00:00-04:00,DEMO,True\n",
        encoding="utf-8",
    )
