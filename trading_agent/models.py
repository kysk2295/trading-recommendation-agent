from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum


class RecommendationState(StrEnum):
    SETUP = "setup"
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    CAUSALITY_EXCLUDED = "causality_excluded"
    STOPPED = "stopped"
    TARGET_1R = "target_1r"
    TARGET_2R = "target_2r"
    TIME_EXIT = "time_exit"


@dataclass(frozen=True, slots=True)
class BarInput:
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    prior_close: float
    average_daily_volume: int
    spread_bps: float
    catalyst: str = ""


@dataclass(frozen=True, slots=True)
class MomentumCandidate:
    symbol: str
    timestamp: dt.datetime
    price: float
    gap_pct: float
    change_pct: float
    relative_volume: float
    cumulative_dollar_volume: float
    spread_bps: float
    catalyst: str


@dataclass(frozen=True, slots=True)
class StrategySignal:
    symbol: str
    timestamp: dt.datetime
    strategy: str
    entry: float
    stop: float
    rationale: str


@dataclass(frozen=True, slots=True)
class TradePlan:
    entry: float
    stop: float
    target_1r: float
    target_2r: float
    risk_per_share: float


@dataclass(frozen=True, slots=True)
class Recommendation:
    recommendation_id: str
    symbol: str
    strategy: str
    created_at: dt.datetime
    entry: float
    stop: float
    target_1r: float
    target_2r: float
    state: RecommendationState
    rationale: str


@dataclass(frozen=True, slots=True)
class RecommendationEvent:
    recommendation_id: str
    occurred_at: dt.datetime
    state: RecommendationState
    price: float | None
    note: str


@dataclass(frozen=True, slots=True)
class RecommendationAlert:
    recommendation_id: str
    queued_at: dt.datetime
    payload_json: str
    card_markdown: str
