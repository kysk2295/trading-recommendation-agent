from __future__ import annotations

import datetime as dt
from typing import Final
from zoneinfo import ZoneInfo

from scr_backtest.kis_intraday import KisMinuteBar
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

SEOUL: Final = ZoneInfo("Asia/Seoul")
BAR_DURATION: Final = dt.timedelta(minutes=1)
MAX_FEED_DELAY: Final = dt.timedelta(minutes=3)
PREMARKET_OPEN: Final = dt.time(4)
DAYTIME_OPEN: Final = dt.time(10)


def regular_session_is_open(observed_at: dt.datetime) -> bool:
    current = observed_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    return bounds is not None and bounds[0] <= current < bounds[1]


def premarket_session_is_open(observed_at: dt.datetime) -> bool:
    current = observed_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    if bounds is None:
        return False
    premarket_open = dt.datetime.combine(
        current.date(),
        PREMARKET_OPEN,
        tzinfo=NEW_YORK,
    )
    return premarket_open <= current < bounds[0]


def daytime_target_session_date(observed_at: dt.datetime) -> dt.date | None:
    target = observed_at.astimezone(SEOUL).date()
    return target if regular_session_bounds(target) is not None else None


def daytime_session_is_open(observed_at: dt.datetime) -> bool:
    current = observed_at.astimezone(SEOUL)
    target = daytime_target_session_date(current)
    if target is None:
        return False
    bounds = regular_session_bounds(target)
    if bounds is None:
        return False
    opened_at = dt.datetime.combine(target, DAYTIME_OPEN, tzinfo=SEOUL)
    premarket_open = dt.datetime.combine(
        target,
        PREMARKET_OPEN,
        tzinfo=NEW_YORK,
    ).astimezone(SEOUL)
    return opened_at <= current < premarket_open


def completed_regular_minutes(bars: tuple[KisMinuteBar, ...], observed_at: dt.datetime) -> tuple[KisMinuteBar, ...]:
    current = observed_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    if bounds is None:
        return ()
    session_open, session_close = bounds
    cutoff = current - BAR_DURATION
    return tuple(
        sorted(
            (
                bar
                for bar in bars
                if session_open <= bar.exchange_timestamp.astimezone(NEW_YORK) < session_close
                and bar.exchange_timestamp.astimezone(NEW_YORK) <= cutoff
            ),
            key=lambda bar: bar.exchange_timestamp,
        )
    )


def session_is_fresh(
    bars: tuple[KisMinuteBar, ...],
    now: dt.datetime | None = None,
    max_delay: dt.timedelta = MAX_FEED_DELAY,
) -> bool:
    if not bars:
        return False
    current = dt.datetime.now(NEW_YORK) if now is None else now.astimezone(NEW_YORK)
    if not regular_session_is_open(current):
        return False
    latest = max(bar.exchange_timestamp.astimezone(NEW_YORK) for bar in bars)
    delay = current - latest
    return latest.date() == current.date() and dt.timedelta(0) <= delay <= max_delay
