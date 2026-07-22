from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest
from trading_agent.swing_new_high_rvol import project_new_high_rvol_signals
from trading_agent.swing_shadow_delivery import project_swing_shadow_cycle_delivery
from trading_agent.swing_shadow_engine import advance_swing_shadow_session
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.swing_shadow_review_store import SwingShadowReviewStore
from trading_agent.swing_shadow_store import SwingShadowStore
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_swing_operating_coordinator import (
    SwingOperatingConfig,
    SwingOperatingPhase,
    SwingOperatingRequest,
    run_us_swing_operating_tick,
)
from trading_agent.us_swing_operating_models import SwingScanCompleted, SwingScanOutcome

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
SIGNAL_SESSION = dt.date(2026, 7, 17)
PLANNED_SESSION = dt.date(2026, 7, 20)
CODE_VERSION = "test_code_v1"


def test_tick_recovers_registration_before_open_and_starts_only_in_regular_session(
    tmp_path: Path,
) -> None:
    # Given: a completed-day signal exists after the operating process restarted.
    config, scanner = _config(tmp_path, {SIGNAL_SESSION: _signal_source()})
    scanner.run(SIGNAL_SESSION)
    open_at, _ = _bounds(PLANNED_SESSION)

    # When: the coordinator ticks before open and then inside the regular session.
    pre_open = run_us_swing_operating_tick(
        SwingOperatingRequest(now=open_at - dt.timedelta(minutes=5), runtime_code_version=CODE_VERSION),
        config,
    )
    regular = run_us_swing_operating_tick(
        SwingOperatingRequest(now=open_at + dt.timedelta(minutes=1), runtime_code_version=CODE_VERSION),
        config,
    )

    # Then: registration is recovered prospectively and the trial starts exactly once.
    assert pre_open.phase is SwingOperatingPhase.PRE_OPEN
    assert pre_open.registered == 1
    assert pre_open.started == 0
    assert regular.phase is SwingOperatingPhase.REGULAR
    assert regular.registered == 0
    assert regular.started == 1
    assert regular.blocked_signal_ids == ()


def test_post_close_tick_scans_finalizes_delivers_reviews_and_replays_exactly(
    tmp_path: Path,
) -> None:
    # Given: a preregistered trial was started in its planned regular session.
    config, scanner = _config(
        tmp_path,
        {
            SIGNAL_SESSION: _signal_source(),
            PLANNED_SESSION: _terminal_source(),
        },
    )
    _, signal_close = _bounds(SIGNAL_SESSION)
    first = run_us_swing_operating_tick(
        SwingOperatingRequest(
            now=signal_close + dt.timedelta(minutes=4),
            runtime_code_version=CODE_VERSION,
        ),
        config,
    )
    open_at, close_at = _bounds(PLANNED_SESSION)
    started = run_us_swing_operating_tick(
        SwingOperatingRequest(now=open_at + dt.timedelta(minutes=1), runtime_code_version=CODE_VERSION),
        config,
    )

    # When: the coordinator ticks twice after the planned session closes.
    completed = run_us_swing_operating_tick(
        SwingOperatingRequest(now=close_at + dt.timedelta(minutes=5), runtime_code_version=CODE_VERSION),
        config,
    )
    replay = run_us_swing_operating_tick(
        SwingOperatingRequest(now=close_at + dt.timedelta(minutes=10), runtime_code_version=CODE_VERSION),
        config,
    )

    # Then: the scanner runs once per source day and every terminal side effect is idempotent.
    assert first.registered == 1
    assert started.started == 1
    assert completed.phase is SwingOperatingPhase.POST_CLOSE
    assert completed.scanner_executed is True
    assert completed.finalized == 1
    assert completed.delivered == 1
    assert completed.reviewed == 1
    assert completed.blocked_signal_ids == ()
    assert replay.scanner_executed is False
    assert replay.finalized == 0
    assert replay.delivered == 0
    assert replay.reviewed == 0
    assert scanner.session_dates == [SIGNAL_SESSION, PLANNED_SESSION]


def test_post_close_tick_does_not_backdate_a_missed_regular_session_start(
    tmp_path: Path,
) -> None:
    # Given: a signal was registered, but no tick ran during its planned regular session.
    config, _ = _config(
        tmp_path,
        {
            SIGNAL_SESSION: _signal_source(),
            PLANNED_SESSION: _terminal_source(),
        },
    )
    _, signal_close = _bounds(SIGNAL_SESSION)
    registered = run_us_swing_operating_tick(
        SwingOperatingRequest(
            now=signal_close + dt.timedelta(minutes=5),
            runtime_code_version=CODE_VERSION,
        ),
        config,
    )
    _, close_at = _bounds(PLANNED_SESSION)

    # When: the next tick arrives only after that session has closed.
    result = run_us_swing_operating_tick(
        SwingOperatingRequest(now=close_at + dt.timedelta(minutes=5), runtime_code_version=CODE_VERSION),
        config,
    )

    # Then: the coordinator records a blocked signal and creates no fake start or terminal evidence.
    assert registered.registered == 1
    assert len(result.blocked_signal_ids) == 1
    assert result.started == 0
    assert result.finalized == 0
    assert result.delivered == 0
    assert result.reviewed == 0


class _FixtureScanner:
    def __init__(
        self,
        sources: dict[dt.date, SwingDailySource],
        shadow: SwingShadowStore,
        delivery: HermesDeliveryStore,
    ) -> None:
        self._sources = sources
        self._shadow = shadow
        self._delivery = delivery
        self.session_dates: list[dt.date] = []

    def run(self, session_date: dt.date) -> SwingScanOutcome:
        source = self._sources[session_date]
        signals = project_new_high_rvol_signals(source)
        with self._shadow.writer() as writer:
            _ = advance_swing_shadow_session(writer, source=source, signals=signals)
        with self._delivery.writer() as writer:
            _ = project_swing_shadow_cycle_delivery(source, signals, writer)
        self.session_dates.append(session_date)
        return SwingScanCompleted(source.observed_at + dt.timedelta(seconds=1))


def _config(
    tmp_path: Path,
    sources: dict[dt.date, SwingDailySource],
) -> tuple[SwingOperatingConfig, _FixtureScanner]:
    experiments = ExperimentLedgerStore(tmp_path / "experiments.sqlite3")
    _ = register_research_hypothesis_manifest(MANIFEST, experiments)
    shadow = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")
    delivery = HermesDeliveryStore(tmp_path / "delivery.sqlite3")
    scanner = _FixtureScanner(sources, shadow, delivery)
    return (
        SwingOperatingConfig(
            experiment_ledger=experiments,
            shadow_ledger=shadow,
            delivery_store=delivery,
            review_store=SwingShadowReviewStore(tmp_path / "reviews.sqlite3"),
            scanner=scanner,
        ),
        scanner,
    )


def _signal_source() -> SwingDailySource:
    sessions = _following_sessions(SIGNAL_SESSION, count=21, backwards=True)
    observed_at = _after_close(SIGNAL_SESSION)
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


def _terminal_source() -> SwingDailySource:
    observed_at = _after_close(PLANNED_SESSION)
    sessions = _following_sessions(PLANNED_SESSION, count=21, backwards=True)
    return SwingDailySource(
        session_date=PLANNED_SESSION,
        observed_at=observed_at,
        universe_id="fixture_universe_v1",
        symbols=("ACME",),
        bars=tuple(
            SwingDailyBar(
                symbol="ACME",
                session_date=session_date,
                observed_at=observed_at,
                open=Decimal("14.8") if session_date == PLANNED_SESSION else Decimal("10"),
                high=(
                    Decimal("15")
                    if session_date == PLANNED_SESSION
                    else Decimal("15.2")
                    if session_date == SIGNAL_SESSION
                    else Decimal("10.2")
                ),
                low=Decimal("14.5") if session_date == PLANNED_SESSION else Decimal("9.9"),
                close=(
                    Decimal("14.9")
                    if session_date == PLANNED_SESSION
                    else Decimal("15")
                    if session_date == SIGNAL_SESSION
                    else Decimal("10")
                ),
                volume=100_000,
            )
            for session_date in sessions
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


def _after_close(session_date: dt.date) -> dt.datetime:
    _, close_at = _bounds(session_date)
    return close_at + dt.timedelta(minutes=5)


def _bounds(session_date: dt.date) -> tuple[dt.datetime, dt.datetime]:
    bounds = regular_session_bounds(session_date)
    assert bounds is not None
    return bounds
