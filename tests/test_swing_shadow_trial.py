from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_models import (
    StrategyLifecycleState,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest
from trading_agent.research_identity_models import AgentOperatingMode
from trading_agent.signal_contract_models import TradeSignalEnvelope
from trading_agent.swing_new_high_rvol import project_new_high_rvol_signals
from trading_agent.swing_shadow_engine import advance_swing_shadow_session
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.swing_shadow_store import ShadowEventKind, SwingShadowReader, SwingShadowStore
from trading_agent.swing_shadow_trial import (
    InvalidSwingShadowTrialSourceError,
    finalize_swing_shadow_trial,
    register_swing_shadow_trial,
    start_swing_shadow_trial,
    swing_shadow_trial_artifact_sha256s,
    swing_shadow_trial_data_version,
    swing_shadow_trial_id,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
SIGNAL_SESSION = dt.date(2026, 7, 17)
CODE_VERSION = "test_code_v1"


def test_registers_one_prospective_trial_and_lifecycle_for_a_shadow_signal(tmp_path: Path) -> None:
    experiments, shadow, signal = _seed_signal(tmp_path)
    planned_start = signal.valid_until.astimezone(NEW_YORK).date()
    open_at, _ = _bounds(planned_start)

    result = register_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        runtime_code_version=CODE_VERSION,
        registered_at=open_at - dt.timedelta(minutes=1),
    )
    replay = register_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        runtime_code_version=CODE_VERSION,
        registered_at=open_at + dt.timedelta(minutes=1),
    )

    assert result.created is True
    assert replay.created is False
    assert replay.registration == result.registration
    assert re.fullmatch(r"swing-shadow-20260717-[0-9a-f]{16}", result.registration.trial_id)
    assert result.registration.trial_id == swing_shadow_trial_id(signal)
    assert result.registration.trial_kind is TrialKind.SHADOW_FORWARD
    assert result.registration.planned_start == planned_start
    assert result.registration.planned_end >= planned_start
    assert result.registration.data_version == swing_shadow_trial_data_version(
        signal,
        shadow.events(signal.signal_id)[0],
    )
    assert experiments.lifecycle_events(result.registration.strategy_version)[0].event.to_state is (
        StrategyLifecycleState.EXPERIMENTAL_SHADOW
    )
    assert experiments.strategy_authority_bindings()[0].binding.operating_mode is (AgentOperatingMode.SHADOW)
    assert len(experiments.trials()) == 1


def test_rejects_new_trial_at_or_after_next_regular_open(tmp_path: Path) -> None:
    experiments, shadow, signal = _seed_signal(tmp_path)
    open_at, _ = _bounds(signal.valid_until.astimezone(NEW_YORK).date())

    with pytest.raises(InvalidSwingShadowTrialSourceError):
        _ = register_swing_shadow_trial(
            experiment_ledger=experiments,
            shadow_ledger=SwingShadowReader(shadow.path),
            signal_id=signal.signal_id,
            runtime_code_version=CODE_VERSION,
            registered_at=open_at,
        )

    assert experiments.trials() == ()


def test_existing_trial_rejects_a_different_runtime_code_version(tmp_path: Path) -> None:
    experiments, shadow, signal = _seed_signal(tmp_path)
    open_at, _ = _bounds(signal.valid_until.astimezone(NEW_YORK).date())
    _ = register_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        runtime_code_version=CODE_VERSION,
        registered_at=open_at - dt.timedelta(minutes=1),
    )

    with pytest.raises(InvalidSwingShadowTrialSourceError):
        _ = register_swing_shadow_trial(
            experiment_ledger=experiments,
            shadow_ledger=SwingShadowReader(shadow.path),
            signal_id=signal.signal_id,
            runtime_code_version="different_code_v1",
            registered_at=open_at + dt.timedelta(minutes=1),
        )


def test_start_is_limited_to_the_planned_regular_session(tmp_path: Path) -> None:
    experiments, shadow, signal = _seed_signal(tmp_path)
    open_at, _ = _bounds(signal.valid_until.astimezone(NEW_YORK).date())
    registration = register_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        runtime_code_version=CODE_VERSION,
        registered_at=open_at - dt.timedelta(minutes=1),
    ).registration

    with pytest.raises(InvalidSwingShadowTrialSourceError):
        _ = start_swing_shadow_trial(
            experiment_ledger=experiments,
            shadow_ledger=SwingShadowReader(shadow.path),
            signal_id=signal.signal_id,
            started_at=open_at - dt.timedelta(seconds=1),
        )

    assert experiments.trial_events(registration.trial_id) == ()


def test_start_and_finalize_require_exact_observed_shadow_terminal(tmp_path: Path) -> None:
    experiments, shadow, signal = _seed_signal(tmp_path)
    planned_start = signal.valid_until.astimezone(NEW_YORK).date()
    open_at, _ = _bounds(planned_start)
    registration = register_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        runtime_code_version=CODE_VERSION,
        registered_at=open_at - dt.timedelta(minutes=1),
    ).registration

    started = start_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        started_at=open_at + dt.timedelta(minutes=1),
    )
    with pytest.raises(InvalidSwingShadowTrialSourceError):
        _ = finalize_swing_shadow_trial(
            experiment_ledger=experiments,
            shadow_ledger=SwingShadowReader(shadow.path),
            signal_id=signal.signal_id,
            finalized_at=open_at + dt.timedelta(minutes=2),
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
    events = shadow.events(signal.signal_id)
    terminal = events[-1]
    finalized = finalize_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        finalized_at=terminal.observed_at + dt.timedelta(minutes=1),
    )
    replay = finalize_swing_shadow_trial(
        experiment_ledger=experiments,
        shadow_ledger=SwingShadowReader(shadow.path),
        signal_id=signal.signal_id,
        finalized_at=terminal.observed_at + dt.timedelta(hours=1),
    )

    assert started.created is True
    assert started.event.event_kind is TrialEventKind.STARTED
    assert terminal.kind is ShadowEventKind.EXPIRED
    assert finalized.created is True
    assert finalized.event.event_kind is TrialEventKind.COMPLETED
    assert finalized.event.artifact_sha256s == swing_shadow_trial_artifact_sha256s(signal, events)
    assert replay.created is False
    assert replay.event == finalized.event
    assert tuple(event.event.event_kind for event in experiments.trial_events(registration.trial_id)) == (
        TrialEventKind.STARTED,
        TrialEventKind.COMPLETED,
    )


def test_trial_import_closure_excludes_operational_modules() -> None:
    script = """
import json
import sys
import trading_agent.swing_shadow_trial
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


def _seed_signal(tmp_path: Path) -> tuple[ExperimentLedgerStore, SwingShadowStore, TradeSignalEnvelope]:
    experiments = ExperimentLedgerStore(tmp_path / "experiments.sqlite3")
    _ = register_research_hypothesis_manifest(MANIFEST, experiments)
    signal_source = _signal_source()
    signal = project_new_high_rvol_signals(signal_source)[0]
    shadow = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")
    with shadow.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=signal_source, signals=(signal,))
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
