from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never, override

from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicTerminalEvidence,
    AlpacaSipDynamicTerminalStatus,
)


class AlpacaSipDynamicBackoffError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic backoff decision is invalid"


class AlpacaSipDynamicBackoffStatus(StrEnum):
    READY = "ready"
    WAIT = "wait"
    BLOCKED_CLOCK_REGRESSION = "blocked_clock_regression"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicBackoffConfig:
    initial_seconds: float
    multiplier: float
    max_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.initial_seconds) is not float
            or not 1.0 <= self.initial_seconds <= 30.0
            or type(self.multiplier) is not float
            or not 1.0 <= self.multiplier <= 4.0
            or type(self.max_seconds) is not float
            or not self.initial_seconds <= self.max_seconds <= 120.0
        ):
            raise AlpacaSipDynamicBackoffError


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicBackoffDecision:
    status: AlpacaSipDynamicBackoffStatus
    eligible_at: dt.datetime | None
    remaining_seconds: float

    def __post_init__(self) -> None:
        if type(self.status) is not AlpacaSipDynamicBackoffStatus or self.remaining_seconds < 0.0:
            raise AlpacaSipDynamicBackoffError
        match self.status:
            case AlpacaSipDynamicBackoffStatus.READY:
                valid = self.remaining_seconds == 0.0 and (self.eligible_at is None or _aware(self.eligible_at))
            case AlpacaSipDynamicBackoffStatus.WAIT:
                valid = self.remaining_seconds > 0.0 and self.eligible_at is not None and _aware(self.eligible_at)
            case AlpacaSipDynamicBackoffStatus.BLOCKED_CLOCK_REGRESSION:
                valid = self.eligible_at is None and self.remaining_seconds == 0.0
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise AlpacaSipDynamicBackoffError


def decide_alpaca_sip_dynamic_backoff(
    history: tuple[AlpacaSipDynamicTerminalEvidence, ...],
    *,
    now: dt.datetime,
    config: AlpacaSipDynamicBackoffConfig,
) -> AlpacaSipDynamicBackoffDecision:
    if (
        type(history) is not tuple
        or any(
            type(item) is not AlpacaSipDynamicTerminalEvidence
            or item.status is not AlpacaSipDynamicTerminalStatus.FAILED
            for item in history
        )
        or history != tuple(sorted(history, key=lambda item: (item.terminal_at, item.connection_epoch)))
        or not _aware(now)
        or type(config) is not AlpacaSipDynamicBackoffConfig
    ):
        raise AlpacaSipDynamicBackoffError
    if not history:
        return AlpacaSipDynamicBackoffDecision(AlpacaSipDynamicBackoffStatus.READY, None, 0.0)
    terminal_at = history[-1].terminal_at
    if now < terminal_at:
        return AlpacaSipDynamicBackoffDecision(
            AlpacaSipDynamicBackoffStatus.BLOCKED_CLOCK_REGRESSION,
            None,
            0.0,
        )
    delay = min(config.initial_seconds * config.multiplier ** (len(history) - 1), config.max_seconds)
    eligible_at = terminal_at + dt.timedelta(seconds=delay)
    remaining = max(0.0, (eligible_at - now).total_seconds())
    status = AlpacaSipDynamicBackoffStatus.WAIT if remaining > 0.0 else AlpacaSipDynamicBackoffStatus.READY
    return AlpacaSipDynamicBackoffDecision(status, eligible_at, remaining)


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicBackoffConfig",
    "AlpacaSipDynamicBackoffDecision",
    "AlpacaSipDynamicBackoffError",
    "AlpacaSipDynamicBackoffStatus",
    "decide_alpaca_sip_dynamic_backoff",
)
