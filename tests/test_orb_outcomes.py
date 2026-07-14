from __future__ import annotations

import dataclasses
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from trading_agent import orb_outcomes


def test_orb_trade_enters_only_after_observed_breakout_and_stops_on_collision() -> None:
    new_york = ZoneInfo("America/New_York")
    session_date = dt.date(2026, 7, 10)
    selected_at = dt.datetime(2026, 7, 10, 9, 36, 30, tzinfo=new_york)
    bars = tuple(
        _bar(
            dt.datetime.combine(session_date, dt.time(9, 30), tzinfo=new_york)
            + dt.timedelta(minutes=offset),
            selected_at,
            offset,
        )
        for offset in range(390)
    )
    selection = orb_outcomes.OrbSelection(
        selected_at,
        "NAS",
        "ORB",
        0.1,
        5_000_000.0,
        20.0,
    )
    config = orb_outcomes.OrbTestConfig(
        range_minutes=5,
        breakout_buffer_bps=5.0,
        volume_multiplier=1.5,
        stop_multiple=1.0,
        target_r=2.0,
    )

    outcome = orb_outcomes.measure_orb_day((selection,), bars, config)

    assert outcome.status is orb_outcomes.OrbOutcomeStatus.STOPPED
    assert outcome.signal_at == selected_at
    assert outcome.entry_at == dt.datetime(
        2026,
        7,
        10,
        9,
        37,
        tzinfo=new_york,
    )
    assert outcome.entry is not None
    assert outcome.stop is not None
    assert outcome.exit_price == outcome.stop
    assert outcome.gross_return == pytest.approx(outcome.stop / outcome.entry - 1.0)


def test_orb_signal_uses_the_later_minute_fetch_observation() -> None:
    new_york = ZoneInfo("America/New_York")
    session_date = dt.date(2026, 7, 10)
    selected_at = dt.datetime(2026, 7, 10, 9, 36, 30, tzinfo=new_york)
    fetched_at = selected_at + dt.timedelta(seconds=15)
    bars = tuple(
        dataclasses.replace(
            _bar(
                dt.datetime.combine(
                    session_date,
                    dt.time(9, 30),
                    tzinfo=new_york,
                )
                + dt.timedelta(minutes=offset),
                fetched_at,
                offset,
            ),
            first_observed_at=fetched_at,
        )
        for offset in range(390)
    )
    selection = orb_outcomes.OrbSelection(
        selected_at,
        "NAS",
        "LATE",
        0.1,
        5_000_000.0,
        20.0,
    )
    config = orb_outcomes.OrbTestConfig(5, 5.0, 1.5, 1.0, 2.0)

    outcome = orb_outcomes.measure_orb_day((selection,), bars, config)

    assert outcome.signal_at == fetched_at
    assert outcome.entry_at == dt.datetime(
        2026,
        7,
        10,
        9,
        37,
        tzinfo=new_york,
    )


def _bar(
    timestamp: dt.datetime,
    first_observed_at: dt.datetime,
    offset: int,
) -> orb_outcomes.OrbBar:
    if offset < 5:
        return orb_outcomes.OrbBar(
            timestamp,
            first_observed_at,
            9.9,
            10.0,
            9.8,
            9.9,
            100,
        )
    if offset == 5:
        return orb_outcomes.OrbBar(
            timestamp,
            first_observed_at,
            9.95,
            10.2,
            9.9,
            10.1,
            200,
        )
    if offset == 7:
        return orb_outcomes.OrbBar(
            timestamp,
            first_observed_at,
            10.01,
            10.6,
            9.7,
            10.1,
            200,
        )
    return orb_outcomes.OrbBar(
        timestamp,
        first_observed_at,
        10.0,
        10.1,
        9.9,
        10.0,
        100,
    )
