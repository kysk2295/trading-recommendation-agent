from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True, slots=True)
class ReplayContext:
    exchange: str
    symbol: str
    observed_at: dt.datetime
    latest_completed_bar_at: dt.datetime
    prior_close: float
    average_daily_volume: int
    spread_bps: float


@dataclass(frozen=True, slots=True)
class ReplayBar:
    exchange: str
    symbol: str
    timestamp: dt.datetime
    first_observed_at: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class ReplaySymbolCoverage:
    exchange: str
    symbol: str
    expected_minutes: int
    archived_minutes: int
    complete: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ReplaySource:
    session_date: dt.date
    contexts: tuple[ReplayContext, ...]
    bars: tuple[ReplayBar, ...]
    coverage: tuple[ReplaySymbolCoverage, ...]


@dataclass(frozen=True, slots=True)
class ReplaySourceRejectedError(RuntimeError):
    reasons: tuple[str, ...]
    session_date: dt.date | None = None

    def __str__(self) -> str:
        return ";".join(self.reasons)


class ChallengerReplayGate(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    strategy: str
    passed: bool
    comparison_eligible: bool = False
    session_date: dt.date | None = None
    reasons: tuple[str, ...]
    input_snapshots: int = 0
    complete_symbols: int = 0
    censored_symbols: int = 0
    recommendations: int = 0
    completed_trades: int = 0
