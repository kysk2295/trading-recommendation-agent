from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from trading_agent.metrics import PaperTrade
from trading_agent.trade_cohort_models import TradeFeatureAssignment


class AdaptiveAction(StrEnum):
    COLLECTING = "collecting"
    SHADOW_CONTINUE = "shadow_continue"
    EARLY_STOP = "early_stop"
    DIAGNOSE = "diagnose"
    COMPARISON_READY = "comparison_ready"
    SUSPEND = "suspend"
    PROMOTION_REVIEW = "promotion_review"


class CohortDimension(StrEnum):
    PRICE = "price"
    OPENING_GAP = "opening_gap"
    VOLUME_TO_ADV = "volume_to_adv"
    DOLLAR_VOLUME = "dollar_volume"


@dataclass(frozen=True, slots=True)
class EvaluatedSession:
    session_date: dt.date
    trades: tuple[PaperTrade, ...]
    regime: str | None
    features: tuple[TradeFeatureAssignment, ...] = ()


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    as_of: dt.date
    strategy_version: str
    evaluator_version: str
    external_promotion_blockers: tuple[str, ...]


class WindowEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    required_sessions: Literal[5, 10, 20, 60]
    observed_sessions: int
    complete: bool
    trade_count: int
    win_rate: float | None
    average_return: float | None
    profit_factor: float | None
    cumulative_return: float | None
    max_drawdown: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None


class RegimeEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    regime: str
    session_count: int
    trade_count: int
    average_return: float | None
    profit_factor: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None


class CohortEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: CohortDimension
    bucket: str
    trade_count: int
    average_return: float | None
    profit_factor: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None


class AdaptiveEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1]
    as_of: dt.date
    strategy_version: str
    evaluator_version: str
    action: AdaptiveAction
    reasons: tuple[str, ...]
    windows: tuple[WindowEvidence, ...]
    regime_coverage: float
    regimes: tuple[RegimeEvidence, ...]
    feature_coverage: float
    gap_feature_coverage: float
    cohorts: tuple[CohortEvidence, ...]
    proof_blockers: tuple[str, ...]
    automatic_state_change_allowed: Literal[False]
