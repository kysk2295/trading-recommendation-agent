from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

from trading_agent.kis_live import premarket_session_is_open, regular_session_is_open
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


@dataclass(frozen=True, slots=True)
class SessionWaitConfig:
    max_wait: dt.timedelta
    poll_seconds: float


@dataclass(frozen=True, slots=True)
class PremarketWaitConfig:
    max_wait: dt.timedelta
    closed_poll_seconds: float
    collection_interval_seconds: float


@dataclass(frozen=True, slots=True)
class PremarketWaitResult:
    opened_at: dt.datetime | None
    exit_codes: tuple[int, ...]


def wait_for_session_open(
    clock: Callable[[], dt.datetime],
    sleeper: Callable[[float], None],
    config: SessionWaitConfig,
) -> dt.datetime | None:
    observed_at = clock()
    deadline = observed_at + config.max_wait
    while not regular_session_is_open(observed_at):
        remaining = (deadline - observed_at).total_seconds()
        if remaining <= 0.0:
            return None
        sleeper(min(config.poll_seconds, remaining))
        observed_at = clock()
    return observed_at


def collect_premarket_until_regular_open(
    clock: Callable[[], dt.datetime],
    sleeper: Callable[[float], None],
    operation: Callable[[], int],
    config: PremarketWaitConfig,
) -> PremarketWaitResult:
    observed_at = clock()
    deadline = observed_at + config.max_wait
    exit_codes: list[int] = []
    while not regular_session_is_open(observed_at):
        remaining = (deadline - observed_at).total_seconds()
        if remaining <= 0.0:
            return PremarketWaitResult(None, tuple(exit_codes))
        if premarket_session_is_open(observed_at):
            exit_codes.append(operation())
            observed_at = clock()
            if regular_session_is_open(observed_at):
                return PremarketWaitResult(observed_at, tuple(exit_codes))
            remaining = (deadline - observed_at).total_seconds()
            if remaining <= 0.0:
                return PremarketWaitResult(None, tuple(exit_codes))
            bounds = regular_session_bounds(
                observed_at.astimezone(NEW_YORK).date()
            )
            match bounds:
                case (session_open, _):
                    delay = min(
                        config.collection_interval_seconds,
                        (session_open - observed_at).total_seconds(),
                    )
                case None:
                    delay = config.closed_poll_seconds
                case unreachable:
                    assert_never(unreachable)
        else:
            delay = config.closed_poll_seconds
        sleeper(min(delay, remaining))
        observed_at = clock()
    return PremarketWaitResult(observed_at, tuple(exit_codes))
