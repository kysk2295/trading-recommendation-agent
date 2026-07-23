from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.challenger_replay_fixtures import write_closed_source_session
from trading_agent.forward_post_session import (
    ForwardPostSessionError,
    ForwardPostSessionStatus,
    close_forward_post_session,
)
from trading_agent.metrics import extract_paper_trades
from trading_agent.metrics_report import write_metrics_report
from trading_agent.scan_cycle import append_cycle_audit
from trading_agent.store import PaperStore

SESSION_DATE = dt.date(2026, 7, 14)
NEW_YORK = ZoneInfo("America/New_York")
AFTER_CLOSE = dt.datetime(2026, 7, 14, 16, 5, tzinfo=NEW_YORK)


def test_clean_session_recovers_missing_post_chain_once_and_replays(
    tmp_path: Path,
) -> None:
    session = _ready_session(tmp_path)
    finalized: list[dt.datetime] = []
    runs: list[dt.datetime] = []

    def finalize(path: Path, observed_at: dt.datetime) -> int:
        assert path == session
        finalized.append(observed_at)
        return 0

    def run(path: Path, observed_at: dt.datetime) -> int:
        runs.append(observed_at)
        _write_metrics_and_terminal(path, observed_at, 0)
        return 0

    recovered = close_forward_post_session(
        session,
        SESSION_DATE,
        minimum_watch_cycles=1,
        observed_at=AFTER_CLOSE,
        finalizer=finalize,
        runner=run,
    )
    replayed = close_forward_post_session(
        session,
        SESSION_DATE,
        minimum_watch_cycles=1,
        observed_at=AFTER_CLOSE + dt.timedelta(minutes=1),
        finalizer=finalize,
        runner=run,
    )

    assert recovered.status is ForwardPostSessionStatus.RECOVERED
    assert replayed.status is ForwardPostSessionStatus.REPLAYED
    assert recovered.watch_cycles == 1
    assert recovered.ranking_cycles == 1
    assert recovered.retry_cycles == 1
    assert recovered.candidate_input_cycles == 1
    assert recovered.candidate_inputs == 1
    assert recovered.causal_bars == 390
    assert recovered.complete_symbols == 1
    assert finalized == [AFTER_CLOSE]
    assert runs == [AFTER_CLOSE]


def test_failed_watch_is_not_repaired_or_finalized(tmp_path: Path) -> None:
    session = _ready_session(tmp_path)
    (session / "watch_cycles.csv").write_text(
        "started_at,exit_code,status\n"
        "2026-07-14T09:35:30-04:00,1,failed\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    with pytest.raises(
        ForwardPostSessionError,
        match="forward_progress_blocked",
    ):
        close_forward_post_session(
            session,
            SESSION_DATE,
            minimum_watch_cycles=1,
            observed_at=AFTER_CLOSE,
            finalizer=lambda _path, _at: calls.append("finalize") or 0,
            runner=lambda _path, _at: calls.append("run") or 0,
        )

    assert calls == []
    assert not (session / "post_session_metrics_cycles.csv").exists()


def test_failed_post_attempt_is_preserved_and_never_retried(
    tmp_path: Path,
) -> None:
    session = _ready_session(tmp_path)
    _write_metrics_and_terminal(session, AFTER_CLOSE, 1)
    calls: list[str] = []

    with pytest.raises(
        ForwardPostSessionError,
        match="post_session_failure_preserved",
    ):
        close_forward_post_session(
            session,
            SESSION_DATE,
            minimum_watch_cycles=1,
            observed_at=AFTER_CLOSE + dt.timedelta(minutes=1),
            finalizer=lambda _path, _at: calls.append("finalize") or 0,
            runner=lambda _path, _at: calls.append("run") or 0,
        )

    assert calls == []


def test_runner_failure_becomes_non_retryable_terminal(
    tmp_path: Path,
) -> None:
    session = _ready_session(tmp_path)
    attempts: list[dt.datetime] = []

    def fail(path: Path, observed_at: dt.datetime) -> int:
        attempts.append(observed_at)
        _write_metrics_and_terminal(path, observed_at, 1)
        return 1

    with pytest.raises(
        ForwardPostSessionError,
        match="post_session_chain_failed",
    ):
        close_forward_post_session(
            session,
            SESSION_DATE,
            minimum_watch_cycles=1,
            observed_at=AFTER_CLOSE,
            finalizer=lambda _path, _at: 0,
            runner=fail,
        )
    with pytest.raises(
        ForwardPostSessionError,
        match="post_session_failure_preserved",
    ):
        close_forward_post_session(
            session,
            SESSION_DATE,
            minimum_watch_cycles=1,
            observed_at=AFTER_CLOSE + dt.timedelta(minutes=1),
            finalizer=lambda _path, _at: 0,
            runner=fail,
        )

    assert attempts == [AFTER_CLOSE]


def test_preclose_recovery_is_blocked_before_mutation(tmp_path: Path) -> None:
    session = _ready_session(tmp_path)
    calls: list[str] = []

    with pytest.raises(
        ForwardPostSessionError,
        match="regular_session_not_closed",
    ):
        close_forward_post_session(
            session,
            SESSION_DATE,
            minimum_watch_cycles=1,
            observed_at=AFTER_CLOSE.replace(hour=15, minute=59),
            finalizer=lambda _path, _at: calls.append("finalize") or 0,
            runner=lambda _path, _at: calls.append("run") or 0,
        )

    assert calls == []


def test_historical_success_is_query_only_replay(tmp_path: Path) -> None:
    session = _ready_session(tmp_path)
    _write_metrics_and_terminal(session, AFTER_CLOSE, 0)
    calls: list[str] = []

    result = close_forward_post_session(
        session,
        SESSION_DATE,
        minimum_watch_cycles=1,
        observed_at=AFTER_CLOSE + dt.timedelta(days=1),
        finalizer=lambda _path, _at: calls.append("finalize") or 0,
        runner=lambda _path, _at: calls.append("run") or 0,
    )

    assert result.status is ForwardPostSessionStatus.REPLAYED
    assert calls == []


def _ready_session(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    write_closed_source_session(
        session,
        include_censored_symbol=False,
        post_session_complete=False,
        session_date=SESSION_DATE,
    )
    (session / "kis_ranking_snapshots.csv").write_text(
        "observed_at,symbol\n2026-07-14T09:35:30-04:00,DEMO\n",
        encoding="utf-8",
    )
    (session / "market_risk_screen.csv").write_text(
        "observed_at,symbol\n2026-07-14T09:35:30-04:00,DEMO\n",
        encoding="utf-8",
    )
    return session


def _write_metrics_and_terminal(
    session: Path,
    observed_at: dt.datetime,
    exit_code: int,
) -> None:
    store = PaperStore(session / "paper_recommendations.sqlite3")
    _ = write_metrics_report(
        session / "paper_metrics",
        extract_paper_trades((store,)),
    )
    append_cycle_audit(
        session / "post_session_metrics_cycles.csv",
        observed_at,
        exit_code,
    )
