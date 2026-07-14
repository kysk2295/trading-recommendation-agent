from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ArtifactChecksum(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str
    size_bytes: int


class SessionQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    forward_day_eligible: bool
    ranking_cycles: int
    ranking_requests: int
    ranking_failures: int
    watch_cycles: int
    failed_watch_cycles: int
    read_retry_cycles: int = 0
    read_retries: int = 0
    read_retry_recoveries: int = 0
    read_retry_failures: int = 0
    archived_bars: int
    recommendations: int
    completed_trades: int
    eligible_completed_trades: int


class MetricSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    side_cost_bps: int
    trade_count: int
    win_rate: float | None
    average_return: float | None
    profit_factor: float | None
    cumulative_return: float | None
    max_drawdown: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None


class PromotionAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    cumulative_forward_days: int
    cumulative_completed_trades: int
    blockers: tuple[str, ...]


class DailyResearchRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1]
    record_id: str
    recorded_at: dt.datetime
    session_date: dt.date
    hypothesis_id: str
    hypothesis: str
    falsification_rule: str
    strategy: str
    strategy_version: str
    strategy_stage: Literal["experimental_shadow"]
    code_version: str
    evaluator_version: str
    data_version: str
    feed_entitlement: str
    parameter_set: tuple[str, ...]
    cost_model: tuple[str, ...]
    portfolio_policy: tuple[str, ...]
    session_quality: SessionQuality
    metrics_20bp: MetricSnapshot
    incidents: tuple[str, ...]
    promotion: PromotionAssessment
    artifact_checksums: tuple[ArtifactChecksum, ...]
