from __future__ import annotations

import datetime as dt
import json
import sqlite3
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.data_capability_models import DataSourceId
from trading_agent.experiment_ledger_keys import experiment_trial_event_key
from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest
from trading_agent.signal_contract_models import TradeSignalEnvelope
from trading_agent.swing_new_high_rvol import project_new_high_rvol_signals
from trading_agent.swing_shadow_engine import advance_swing_shadow_session
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.swing_shadow_review_models import (
    CURRENT_SWING_SHADOW_REVIEWER_VERSION,
    SwingShadowReviewerAction,
    SwingShadowReviewEvent,
)
from trading_agent.swing_shadow_review_store import (
    SwingShadowReviewReader,
    SwingShadowReviewStore,
    SwingShadowReviewWriterLeaseUnavailableError,
)
from trading_agent.swing_shadow_reviewer import (
    InvalidSwingShadowReviewError,
    review_swing_shadow_trial,
)
from trading_agent.swing_shadow_store import (
    ShadowEventKind,
    SwingShadowEvent,
    SwingShadowReader,
    SwingShadowStore,
)
from trading_agent.swing_shadow_trial import (
    finalize_swing_shadow_trial,
    register_swing_shadow_trial,
    start_swing_shadow_trial,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
SIGNAL_SESSION = dt.date(2026, 7, 17)
CODE_VERSION = "test_code_v1"


def test_reviewer_rechecks_completed_shadow_evidence_without_any_authority(tmp_path: Path) -> None:
    experiments, shadow, signal, terminal = _completed_trial(tmp_path)
    reviews = SwingShadowReviewStore(tmp_path / "reviews.sqlite3")

    result = review_swing_shadow_trial(
        experiment_ledger=ExperimentLedgerReader(experiments.path),
        shadow_ledger=SwingShadowReader(shadow.path),
        reviews=reviews,
        signal_id=signal.signal_id,
        reviewed_at=terminal.observed_at + dt.timedelta(minutes=2),
    )
    replay = review_swing_shadow_trial(
        experiment_ledger=ExperimentLedgerReader(experiments.path),
        shadow_ledger=SwingShadowReader(shadow.path),
        reviews=reviews,
        signal_id=signal.signal_id,
        reviewed_at=terminal.observed_at + dt.timedelta(hours=1),
    )

    assert result.created is True
    assert replay.created is False
    assert replay.event == result.event
    assert result.event.reviewer_action is SwingShadowReviewerAction.CONTINUE_COLLECTION
    assert result.event.terminal_kind is ShadowEventKind.EXPIRED
    assert result.event.automatic_state_change_allowed is False
    assert result.event.order_authority_change_allowed is False
    assert result.event.allocation_change_allowed is False
    assert result.event.blockers == (
        "automatic_state_change_forbidden",
        "cost_model_unmodeled",
        "forward_sample_insufficient",
        "paper_authority_forbidden",
    )
    assert len(SwingShadowReviewReader(reviews.path).events()) == 1


def test_reviewer_rejects_an_open_trial_without_creating_a_review_ledger(tmp_path: Path) -> None:
    experiments, shadow, signal = _registered_trial(tmp_path)
    reviews = SwingShadowReviewStore(tmp_path / "reviews.sqlite3")

    with pytest.raises(InvalidSwingShadowReviewError):
        _ = review_swing_shadow_trial(
            experiment_ledger=ExperimentLedgerReader(experiments.path),
            shadow_ledger=SwingShadowReader(shadow.path),
            reviews=reviews,
            signal_id=signal.signal_id,
            reviewed_at=signal.valid_until,
        )

    assert not reviews.path.exists()


def test_reviewer_rejects_a_completed_event_with_different_terminal_artifacts(tmp_path: Path) -> None:
    experiments, shadow, signal = _registered_trial(tmp_path)
    planned_start = signal.valid_until.astimezone(NEW_YORK).date()
    open_at, _ = _bounds(planned_start)
    started = start_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        started_at=open_at + dt.timedelta(minutes=1),
    )
    terminal_source = _session_source(
        planned_start,
        open_price=Decimal("14.80"),
        high=Decimal("15"),
        low=Decimal("14.50"),
        close=Decimal("14.90"),
    )
    with shadow.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=terminal_source)
    terminal = shadow.events(signal.signal_id)[-1]
    registration = experiments.trials()[0].registration
    with experiments.writer() as writer:
        assert writer.append_trial_event(
            ExperimentTrialEvent(
                trial_id=registration.trial_id,
                sequence=2,
                event_kind=TrialEventKind.COMPLETED,
                occurred_at=terminal.observed_at + dt.timedelta(minutes=1),
                artifact_sha256s=("a" * 64,),
                reason_codes=(),
                previous_event_key=experiment_trial_event_key(started.event),
            )
        )

    reviews = SwingShadowReviewStore(tmp_path / "reviews.sqlite3")
    with pytest.raises(InvalidSwingShadowReviewError):
        _ = review_swing_shadow_trial(
            experiment_ledger=ExperimentLedgerReader(experiments.path),
            shadow_ledger=SwingShadowReader(shadow.path),
            reviews=reviews,
            signal_id=signal.signal_id,
            reviewed_at=terminal.observed_at + dt.timedelta(minutes=2),
        )

    assert not reviews.path.exists()


def test_review_store_is_private_append_only_and_query_only(tmp_path: Path) -> None:
    store = SwingShadowReviewStore(tmp_path / "reviews.sqlite3")
    event = SwingShadowReviewEvent(
        signal_id="swing-signal-1",
        trial_id="swing-trial-1",
        strategy_version="new_high_rvol_20d_1p5_v1",
        experiment_scope_key="a" * 64,
        terminal_event_key="b" * 64,
        artifact_sha256s=("c" * 64,),
        terminal_kind=ShadowEventKind.EXPIRED,
        reviewer_version=CURRENT_SWING_SHADOW_REVIEWER_VERSION,
        reviewer_action=SwingShadowReviewerAction.CONTINUE_COLLECTION,
        reasons=("terminal_expired",),
        blockers=(
            "automatic_state_change_forbidden",
            "cost_model_unmodeled",
            "forward_sample_insufficient",
            "paper_authority_forbidden",
        ),
        reviewed_at=dt.datetime(2026, 7, 20, 20, tzinfo=dt.UTC),
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
        allocation_change_allowed=False,
    )

    with store.writer() as writer:
        assert writer.append_event(event) is True
        assert writer.append_event(event) is False

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("UPDATE swing_shadow_review_events SET payload_json = '{}' ")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("DELETE FROM swing_shadow_review_events")
    with SwingShadowReviewReader(store.path).reader_connection() as connection, pytest.raises(sqlite3.OperationalError):
        _ = connection.execute("DELETE FROM swing_shadow_review_events")
    with store.writer(), pytest.raises(SwingShadowReviewWriterLeaseUnavailableError), store.writer():
        pass


def test_reviewer_import_closure_excludes_operational_modules() -> None:
    script = """
import json
import sys
import trading_agent.swing_shadow_reviewer
print(json.dumps(sorted(name for name in sys.modules if name.startswith('trading_agent.'))))
"""
    completed = subprocess.run(
        (sys.executable, "-c", script),
        check=True,
        capture_output=True,
        text=True,
    )
    loaded_modules = json.loads(completed.stdout)
    forbidden = (
        "alpaca",
        "paper",
        "broker",
        "execution",
        "credential",
        "provider",
        "lifecycle_controller",
        "portfolio_manager",
    )

    assert not {module for module in loaded_modules if any(marker in module for marker in forbidden)}


def _completed_trial(
    tmp_path: Path,
) -> tuple[ExperimentLedgerStore, SwingShadowStore, TradeSignalEnvelope, SwingShadowEvent]:
    experiments, shadow, signal = _registered_trial(tmp_path)
    planned_start = signal.valid_until.astimezone(NEW_YORK).date()
    open_at, _ = _bounds(planned_start)
    _ = start_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        started_at=open_at + dt.timedelta(minutes=1),
    )
    terminal_source = _session_source(
        planned_start,
        open_price=Decimal("14.80"),
        high=Decimal("15"),
        low=Decimal("14.50"),
        close=Decimal("14.90"),
    )
    with shadow.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=terminal_source)
    terminal = shadow.events(signal.signal_id)[-1]
    _ = finalize_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        finalized_at=terminal.observed_at + dt.timedelta(minutes=1),
    )
    return experiments, shadow, signal, terminal


def _registered_trial(tmp_path: Path) -> tuple[ExperimentLedgerStore, SwingShadowStore, TradeSignalEnvelope]:
    experiments = ExperimentLedgerStore(tmp_path / "experiments.sqlite3")
    _ = register_research_hypothesis_manifest(MANIFEST, experiments)
    source = _signal_source()
    signal = project_new_high_rvol_signals(source)[0]
    shadow = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")
    with shadow.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=source, signals=(signal,))
    open_at, _ = _bounds(signal.valid_until.astimezone(NEW_YORK).date())
    _ = register_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        runtime_code_version=CODE_VERSION,
        registered_at=open_at - dt.timedelta(minutes=1),
    )
    return experiments, shadow, signal


def _signal_source() -> SwingDailySource:
    sessions = _following_sessions(SIGNAL_SESSION, count=21, backwards=True)
    observed_at = _observed_after_close(SIGNAL_SESSION)
    bars = tuple(
        SwingDailyBar(
            symbol="ACME",
            session_date=session_date,
            observed_at=observed_at,
            open=Decimal("10"),
            high=Decimal("15.2") if index == len(sessions) - 1 else Decimal("10.2"),
            low=Decimal("9.9"),
            close=Decimal("15") if index == len(sessions) - 1 else Decimal("10"),
            volume=200_000 if index == len(sessions) - 1 else 100_000,
        )
        for index, session_date in enumerate(sessions)
    )
    return SwingDailySource(
        session_date=SIGNAL_SESSION,
        observed_at=observed_at,
        source_id=DataSourceId(provider="fixture", feed="completed_daily"),
        universe_id="fixture_universe_v1",
        symbols=("ACME",),
        bars=bars,
    )


def _session_source(
    session_date: dt.date,
    *,
    open_price: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
) -> SwingDailySource:
    observed_at = _observed_after_close(session_date)
    return SwingDailySource(
        session_date=session_date,
        observed_at=observed_at,
        source_id=DataSourceId(provider="fixture", feed="completed_daily"),
        universe_id="fixture_universe_v1",
        symbols=("ACME",),
        bars=(
            SwingDailyBar(
                symbol="ACME",
                session_date=session_date,
                observed_at=observed_at,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=100_000,
            ),
        ),
    )


def _following_sessions(
    session_date: dt.date,
    *,
    count: int,
    backwards: bool = False,
) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = session_date
    increment = -1 if backwards else 1
    for _ in range(100):
        if regular_session_bounds(current) is not None:
            sessions.append(current)
            if len(sessions) == count:
                return tuple(reversed(sessions)) if backwards else tuple(sessions)
        current += dt.timedelta(days=increment)
    raise AssertionError("fixture could not find enough regular sessions")


def _observed_after_close(session_date: dt.date) -> dt.datetime:
    _, close_at = _bounds(session_date)
    return close_at + dt.timedelta(minutes=5)


def _bounds(session_date: dt.date) -> tuple[dt.datetime, dt.datetime]:
    bounds = regular_session_bounds(session_date)
    assert bounds is not None
    return bounds
