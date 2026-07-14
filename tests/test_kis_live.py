from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from scr_backtest.kis_intraday import KisMinuteBar
from trading_agent import kis_live


def test_session_is_fresh_rejects_a_previous_market_day() -> None:
    new_york = ZoneInfo("America/New_York")
    stale = KisMinuteBar(
        dt.datetime(2026, 7, 10, 15, 59, tzinfo=new_york),
        dt.datetime(2026, 7, 11, 4, 59, tzinfo=ZoneInfo("Asia/Seoul")),
        10.0,
        10.1,
        9.9,
        10.0,
        100,
        1000,
    )

    assert not kis_live.session_is_fresh((stale,), dt.datetime(2026, 7, 12, 10, 0, tzinfo=new_york))
    assert kis_live.session_is_fresh((stale,), dt.datetime(2026, 7, 10, 15, 59, 30, tzinfo=new_york))


def test_regular_session_gate_rejects_weekends_and_accepts_market_hours() -> None:
    new_york = ZoneInfo("America/New_York")

    assert not kis_live.regular_session_is_open(dt.datetime(2026, 7, 12, 10, 0, tzinfo=new_york))
    assert kis_live.regular_session_is_open(dt.datetime(2026, 7, 10, 10, 0, tzinfo=new_york))


def test_premarket_gate_accepts_only_published_trading_days_before_open() -> None:
    # Given: one published Monday around the premarket and regular-open boundary.
    new_york = ZoneInfo("America/New_York")

    # When/Then: 04:00~09:29 is premarket, while 09:30 and weekends are not.
    assert kis_live.premarket_session_is_open(
        dt.datetime(2026, 7, 13, 4, 0, tzinfo=new_york)
    )
    assert kis_live.premarket_session_is_open(
        dt.datetime(2026, 7, 13, 9, 29, tzinfo=new_york)
    )
    assert not kis_live.premarket_session_is_open(
        dt.datetime(2026, 7, 13, 9, 30, tzinfo=new_york)
    )
    assert not kis_live.premarket_session_is_open(
        dt.datetime(2026, 7, 12, 8, 0, tzinfo=new_york)
    )


def test_daytime_gate_maps_seoul_session_to_the_target_new_york_date() -> None:
    # Given: KIS daytime boundaries during US daylight saving time.
    seoul = ZoneInfo("Asia/Seoul")

    # When/Then: 10:00~16:59 KST maps to the same dated NY trading session.
    opened = dt.datetime(2026, 7, 13, 10, 0, tzinfo=seoul)
    before_close = dt.datetime(2026, 7, 13, 16, 59, tzinfo=seoul)
    at_close = dt.datetime(2026, 7, 13, 17, 0, tzinfo=seoul)
    assert kis_live.daytime_target_session_date(opened) == dt.date(2026, 7, 13)
    assert kis_live.daytime_session_is_open(opened)
    assert kis_live.daytime_session_is_open(before_close)
    assert not kis_live.daytime_session_is_open(at_close)


def test_daytime_gate_uses_the_later_winter_close_and_rejects_holidays() -> None:
    # Given: standard-time and published holiday KIS daytime observations.
    seoul = ZoneInfo("Asia/Seoul")

    # When/Then: winter closes at 18:00 KST and the calendar remains fail-closed.
    assert kis_live.daytime_session_is_open(
        dt.datetime(2026, 12, 21, 17, 59, tzinfo=seoul)
    )
    assert not kis_live.daytime_session_is_open(
        dt.datetime(2026, 12, 21, 18, 0, tzinfo=seoul)
    )
    assert not kis_live.daytime_session_is_open(
        dt.datetime(2026, 12, 25, 11, 0, tzinfo=seoul)
    )


def test_regular_session_gate_rejects_a_published_nyse_holiday() -> None:
    new_york = ZoneInfo("America/New_York")

    assert not kis_live.regular_session_is_open(
        dt.datetime(2026, 9, 7, 10, 0, tzinfo=new_york)
    )


def test_regular_session_gate_closes_at_one_on_a_published_early_close() -> None:
    new_york = ZoneInfo("America/New_York")

    assert kis_live.regular_session_is_open(
        dt.datetime(2026, 11, 27, 12, 59, tzinfo=new_york)
    )
    assert not kis_live.regular_session_is_open(
        dt.datetime(2026, 11, 27, 13, 0, tzinfo=new_york)
    )


def test_regular_session_gate_fails_closed_outside_the_published_years() -> None:
    new_york = ZoneInfo("America/New_York")

    assert not kis_live.regular_session_is_open(
        dt.datetime(2029, 1, 2, 10, 0, tzinfo=new_york)
    )


def test_completed_minutes_exclude_bars_after_an_early_close() -> None:
    new_york = ZoneInfo("America/New_York")
    seoul = ZoneInfo("Asia/Seoul")
    before_close = KisMinuteBar(
        dt.datetime(2026, 11, 27, 12, 59, tzinfo=new_york),
        dt.datetime(2026, 11, 28, 2, 59, tzinfo=seoul),
        10.0,
        10.1,
        9.9,
        10.0,
        100,
        1000,
    )
    after_close = KisMinuteBar(
        dt.datetime(2026, 11, 27, 13, 1, tzinfo=new_york),
        dt.datetime(2026, 11, 28, 3, 1, tzinfo=seoul),
        10.0,
        10.1,
        9.9,
        10.0,
        100,
        1000,
    )

    completed = kis_live.completed_regular_minutes(
        (before_close, after_close),
        dt.datetime(2026, 11, 27, 14, 0, tzinfo=new_york),
    )

    assert completed == (before_close,)
