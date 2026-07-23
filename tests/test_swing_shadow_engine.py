from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.data_capability_models import DataSourceId
from trading_agent.swing_new_high_rvol import project_new_high_rvol_signals
from trading_agent.swing_shadow_engine import advance_swing_shadow_session
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.swing_shadow_store import (
    InvalidSwingShadowLedgerError,
    ShadowEventKind,
    SwingShadowConflictError,
    SwingShadowEvent,
    SwingShadowStore,
)
from trading_agent.us_equity_calendar import regular_session_bounds

SIGNAL_SESSION = dt.date(2026, 7, 15)


def test_enters_then_stops_when_same_daily_bar_reaches_both_stop_and_target(
    tmp_path: Path,
) -> None:
    signal_source = _signal_source()
    signal = project_new_high_rvol_signals(signal_source)[0]
    entry_date = _next_session(SIGNAL_SESSION)
    store = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")

    with store.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=signal_source, signals=(signal,))
        appended = advance_swing_shadow_session(
            writer,
            source=_session_source(
                entry_date,
                open_price=Decimal("15.10"),
                high=Decimal("18"),
                low=Decimal("13"),
                close=Decimal("15"),
            ),
        )

    events = store.events(signal.signal_id)
    assert tuple(event.kind for event in events) == (
        ShadowEventKind.SIGNAL_CREATED,
        ShadowEventKind.ENTRY_FILLED,
        ShadowEventKind.STOPPED,
    )
    assert tuple(event.kind for event in appended) == (
        ShadowEventKind.ENTRY_FILLED,
        ShadowEventKind.STOPPED,
    )
    assert events[1].price == Decimal("15.10")
    assert events[2].price == Decimal("13.86900")


def test_targets_after_entry_when_stop_is_not_reached(tmp_path: Path) -> None:
    signal_source = _signal_source()
    signal = project_new_high_rvol_signals(signal_source)[0]
    store = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")

    with store.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=signal_source, signals=(signal,))
        _ = advance_swing_shadow_session(
            writer,
            source=_session_source(
                _next_session(SIGNAL_SESSION),
                open_price=Decimal("15"),
                high=Decimal("18"),
                low=Decimal("14"),
                close=Decimal("17.5"),
            ),
        )

    assert tuple(event.kind for event in store.events(signal.signal_id)) == (
        ShadowEventKind.SIGNAL_CREATED,
        ShadowEventKind.ENTRY_FILLED,
        ShadowEventKind.TARGETED,
    )


def test_expires_unfilled_signal_after_its_next_session_validity(tmp_path: Path) -> None:
    signal_source = _signal_source()
    signal = project_new_high_rvol_signals(signal_source)[0]
    store = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")

    with store.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=signal_source, signals=(signal,))
        _ = advance_swing_shadow_session(
            writer,
            source=_session_source(
                _next_session(SIGNAL_SESSION),
                open_price=Decimal("14.80"),
                high=Decimal("15"),
                low=Decimal("14.50"),
                close=Decimal("14.90"),
            ),
        )

    assert tuple(event.kind for event in store.events(signal.signal_id)) == (
        ShadowEventKind.SIGNAL_CREATED,
        ShadowEventKind.EXPIRED,
    )


def test_time_exits_after_ten_completed_sessions_without_a_barrier(tmp_path: Path) -> None:
    signal_source = _signal_source()
    signal = project_new_high_rvol_signals(signal_source)[0]
    store = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")
    entry_date = _next_session(SIGNAL_SESSION)
    sessions = (entry_date, *_following_sessions(entry_date, count=10))
    appended: tuple[SwingShadowEvent, ...] | None = None

    with store.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=signal_source, signals=(signal,))
        for index, session_date in enumerate(sessions):
            appended = advance_swing_shadow_session(
                writer,
                source=_session_source(
                    session_date,
                    open_price=Decimal("15.10") if index == 0 else Decimal("15"),
                    high=Decimal("15.50"),
                    low=Decimal("14"),
                    close=Decimal("15"),
                ),
            )

    assert tuple(event.kind for event in store.events(signal.signal_id)) == (
        ShadowEventKind.SIGNAL_CREATED,
        ShadowEventKind.ENTRY_FILLED,
        ShadowEventKind.TIME_EXIT,
    )
    assert appended is not None
    assert appended[-1].kind is ShadowEventKind.TIME_EXIT
    assert appended[-1].price == Decimal("15")


def test_replay_is_idempotent_and_changed_signal_payload_conflicts(tmp_path: Path) -> None:
    signal_source = _signal_source()
    signal = project_new_high_rvol_signals(signal_source)[0]
    entry_source = _session_source(
        _next_session(SIGNAL_SESSION),
        open_price=Decimal("15"),
        high=Decimal("15.50"),
        low=Decimal("14"),
        close=Decimal("15"),
    )
    store = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")

    with store.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=signal_source, signals=(signal,))
        first = advance_swing_shadow_session(writer, source=entry_source)
        replay = advance_swing_shadow_session(writer, source=entry_source)
        changed = signal.model_copy(update={"entry_price": Decimal("15.08")})
        with pytest.raises(SwingShadowConflictError):
            _ = advance_swing_shadow_session(
                writer,
                source=signal_source,
                signals=(changed,),
            )

    assert tuple(event.kind for event in first) == (ShadowEventKind.ENTRY_FILLED,)
    assert replay == ()


def test_rejects_revised_source_evidence_under_the_same_logical_signal_id(
    tmp_path: Path,
) -> None:
    source = _signal_source()
    revised_source = source.model_copy(update={"observed_at": source.observed_at + dt.timedelta(minutes=1)})
    first = project_new_high_rvol_signals(source)[0]
    revised = project_new_high_rvol_signals(revised_source)[0]
    store = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")

    with store.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=source, signals=(first,))
        with pytest.raises(SwingShadowConflictError):
            _ = advance_swing_shadow_session(
                writer,
                source=revised_source,
                signals=(revised,),
            )

    assert len(store.signals()) == 1


def test_writer_rejects_a_symlink_lock_without_touching_its_target(tmp_path: Path) -> None:
    store = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")
    victim = tmp_path / "victim"
    victim.write_text("unchanged", encoding="utf-8")
    victim.chmod(0o644)
    Path(f"{store.path}.writer.lock").symlink_to(victim)

    with pytest.raises(InvalidSwingShadowLedgerError), store.writer():
        pass

    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_ledger_is_private_and_append_only(tmp_path: Path) -> None:
    signal_source = _signal_source()
    signal = project_new_high_rvol_signals(signal_source)[0]
    store = SwingShadowStore(tmp_path / "swing-shadow.sqlite3")

    with store.writer() as writer:
        _ = advance_swing_shadow_session(writer, source=signal_source, signals=(signal,))

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("DELETE FROM swing_shadow_events")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("UPDATE swing_shadow_signals SET payload_json = '{}' ")


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
        universe_id="fixture-universe-v1",
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
        universe_id="fixture-universe-v1",
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


def _observed_after_close(session_date: dt.date) -> dt.datetime:
    bounds = regular_session_bounds(session_date)
    assert bounds is not None
    return bounds[1] + dt.timedelta(minutes=5)


def _next_session(session_date: dt.date) -> dt.date:
    return _following_sessions(session_date, count=1)[0]


def _following_sessions(
    session_date: dt.date,
    *,
    count: int,
    backwards: bool = False,
) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = session_date if backwards else session_date + dt.timedelta(days=1)
    direction = -1 if backwards else 1
    while len(sessions) < count:
        if regular_session_bounds(current) is not None:
            sessions.append(current)
        current += dt.timedelta(days=direction)
    return tuple(reversed(sessions)) if backwards else tuple(sessions)
