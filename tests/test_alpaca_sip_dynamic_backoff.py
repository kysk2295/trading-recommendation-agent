from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.alpaca_sip_dynamic_backoff import (
    AlpacaSipDynamicBackoffConfig,
    AlpacaSipDynamicBackoffError,
    AlpacaSipDynamicBackoffStatus,
    decide_alpaca_sip_dynamic_backoff,
)
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicTerminalEvidence,
    AlpacaSipDynamicTerminalStatus,
)

_NOW = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)
_CONFIG = AlpacaSipDynamicBackoffConfig(1.0, 2.0, 4.0)


def test_empty_history_is_ready_without_delay() -> None:
    decision = decide_alpaca_sip_dynamic_backoff((), now=_NOW, config=_CONFIG)

    assert decision.status is AlpacaSipDynamicBackoffStatus.READY
    assert decision.eligible_at is None
    assert decision.remaining_seconds == 0.0


def test_failed_history_uses_bounded_exponential_delay() -> None:
    first = decide_alpaca_sip_dynamic_backoff(
        (_failed(1, _NOW),),
        now=_NOW + dt.timedelta(milliseconds=250),
        config=_CONFIG,
    )
    third = decide_alpaca_sip_dynamic_backoff(
        tuple(_failed(attempt, _NOW) for attempt in range(1, 4)),
        now=_NOW + dt.timedelta(milliseconds=250),
        config=_CONFIG,
    )

    assert first.status is AlpacaSipDynamicBackoffStatus.WAIT
    assert first.eligible_at == _NOW + dt.timedelta(seconds=1)
    assert first.remaining_seconds == 0.75
    assert third.status is AlpacaSipDynamicBackoffStatus.WAIT
    assert third.eligible_at == _NOW + dt.timedelta(seconds=4)
    assert third.remaining_seconds == 3.75


def test_elapsed_backoff_is_ready() -> None:
    decision = decide_alpaca_sip_dynamic_backoff(
        (_failed(1, _NOW),),
        now=_NOW + dt.timedelta(seconds=1),
        config=_CONFIG,
    )

    assert decision.status is AlpacaSipDynamicBackoffStatus.READY
    assert decision.eligible_at == _NOW + dt.timedelta(seconds=1)
    assert decision.remaining_seconds == 0.0


def test_clock_regression_blocks_without_waiting() -> None:
    decision = decide_alpaca_sip_dynamic_backoff(
        (_failed(1, _NOW),),
        now=_NOW - dt.timedelta(microseconds=1),
        config=_CONFIG,
    )

    assert decision.status is AlpacaSipDynamicBackoffStatus.BLOCKED_CLOCK_REGRESSION
    assert decision.eligible_at is None
    assert decision.remaining_seconds == 0.0


@pytest.mark.parametrize(
    ("initial_seconds", "multiplier", "max_seconds"),
    ((0.0, 2.0, 4.0), (1.0, 0.5, 4.0), (2.0, 2.0, 1.0)),
)
def test_invalid_config_fails_closed(
    initial_seconds: float,
    multiplier: float,
    max_seconds: float,
) -> None:
    with pytest.raises(AlpacaSipDynamicBackoffError):
        _ = AlpacaSipDynamicBackoffConfig(initial_seconds, multiplier, max_seconds)


def _failed(attempt: int, terminal_at: dt.datetime) -> AlpacaSipDynamicTerminalEvidence:
    return AlpacaSipDynamicTerminalEvidence(
        "a" * 64,
        f"{attempt:032x}",
        terminal_at,
        AlpacaSipDynamicTerminalStatus.FAILED,
        (),
        f"{attempt:064x}",
    )
