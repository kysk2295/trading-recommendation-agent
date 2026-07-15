from __future__ import annotations

import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import ExperimentScope


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
    candidate_input_cycles: int = 0
    candidate_input_selections: int = 0
    candidate_inputs: int = 0
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

    schema_version: Literal[2]
    record_id: str
    recorded_at: dt.datetime
    session_date: dt.date
    hypothesis_id: str
    hypothesis: str
    falsification_rule: str
    strategy: str
    strategy_version: str
    strategy_stage: Literal["experimental_shadow"]
    experiment_scope: ExperimentScope
    experiment_scope_key: str
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

    @model_validator(mode="after")
    def validate_experiment_scope(self) -> Self:
        if (
            self.experiment_scope.hypothesis_id != self.hypothesis_id
            or self.experiment_scope_key != experiment_scope_key(self.experiment_scope)
        ):
            raise ValueError("daily research record experiment scope does not match its hypothesis")
        return self
