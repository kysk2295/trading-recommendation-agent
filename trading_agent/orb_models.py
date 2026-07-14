from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum


class OrbOutcomeStatus(StrEnum):
    CENSORED = "censored"
    NO_SIGNAL = "no_signal"
    RISK_REJECTED = "risk_rejected"
    NO_ENTRY = "no_entry"
    INVALIDATED = "invalidated"
    STOPPED = "stopped"
    TARGET = "target"
    TIME_EXIT = "time_exit"


@dataclass(frozen=True, slots=True)
class OrbSelection:
    observed_at: dt.datetime
    exchange: str
    symbol: str
    change_pct: float
    dollar_volume: float
    spread_bps: float


@dataclass(frozen=True, slots=True)
class OrbBar:
    timestamp: dt.datetime
    first_observed_at: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class OrbTestConfig:
    range_minutes: int
    breakout_buffer_bps: float
    volume_multiplier: float
    stop_multiple: float
    target_r: float
    max_risk_pct: float = 0.05
    max_spread_bps: float = 100.0


@dataclass(frozen=True, slots=True)
class OrbOutcome:
    config: OrbTestConfig
    observed_at: dt.datetime
    exchange: str
    symbol: str
    change_pct: float
    dollar_volume: float
    spread_bps: float
    complete: bool
    status: OrbOutcomeStatus
    signal_at: dt.datetime | None
    entry_at: dt.datetime | None
    exit_at: dt.datetime | None
    entry: float | None
    stop: float | None
    target: float | None
    exit_price: float | None
    gross_return: float | None
    portfolio_selected: bool = False
