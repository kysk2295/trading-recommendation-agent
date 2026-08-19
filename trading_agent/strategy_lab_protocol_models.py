from __future__ import annotations

import datetime as dt
import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.strategy_lab_errors import StrategyLabModelError
from trading_agent.strategy_lab_types import (
    EvidenceMode,
    SignalDirection,
    StrategyLabAdaptation,
    StrategyLabId,
    StrategyLabOutcome,
)


class StrategyLabHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    hypothesis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    lab_id: StrategyLabId
    parent_node_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    adaptation: StrategyLabAdaptation
    statement: str = Field(min_length=1)
    falsification_rule: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if (self.adaptation is StrategyLabAdaptation.INITIAL) != (self.parent_node_id is None):
            raise StrategyLabModelError("invalid strategy lab hypothesis lineage")
        return self


class StrategyLabProtocolBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    lab_id: StrategyLabId
    hypothesis: StrategyLabHypothesis
    dataset_id: str = Field(min_length=1)
    feature_name: str = Field(min_length=1)
    target_name: str = Field(min_length=1)
    direction: SignalDirection
    thresholds: tuple[float, ...] = Field(min_length=1)
    selected_threshold: float
    economic_mechanism: str = Field(min_length=1)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_mode: EvidenceMode
    period_start: dt.date
    period_end: dt.date
    source_ref: str = Field(min_length=1)
    cost_bps: int = Field(ge=0, le=1_000)
    observation_count: int = Field(ge=1)
    minimum_selected_observations: Literal[4] = 4
    primary_metric: Literal["net_excess_return_ci95"] = "net_excess_return_ci95"
    search_family_size: int = Field(ge=1)
    available_at: dt.datetime
    frozen_at: dt.datetime

    @model_validator(mode="after")
    def validate_protocol_body(self) -> Self:
        if (
            self.hypothesis.lab_id is not self.lab_id
            or self.selected_threshold not in self.thresholds
            or not all(math.isfinite(value) for value in self.thresholds)
            or self.search_family_size != len(self.thresholds)
            or self.period_end < self.period_start
            or self.available_at.tzinfo is None
            or self.frozen_at.tzinfo is None
            or self.frozen_at < self.available_at
        ):
            raise StrategyLabModelError("invalid frozen strategy lab protocol")
        return self


class StrategyLabProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    protocol_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    body: StrategyLabProtocolBody


class StrategyLabStatisticalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    protocol_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: StrategyLabOutcome
    reason_codes: tuple[str, ...]
    selected_observations: int = Field(ge=0)
    net_excess_return_mean: float | None
    ci95_lower: float | None
    ci95_upper: float | None
    evaluated_at: dt.datetime

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        metrics = (
            self.net_excess_return_mean,
            self.ci95_lower,
            self.ci95_upper,
        )
        has_metrics = all(value is not None for value in metrics)
        has_no_metrics = all(value is None for value in metrics)
        if (
            self.evaluated_at.tzinfo is None
            or not self.reason_codes
            or not (has_metrics or has_no_metrics)
            or any(value is not None and not math.isfinite(value) for value in metrics)
        ):
            raise StrategyLabModelError("strategy lab result must be timestamped")
        if self.ci95_lower is not None and self.ci95_upper is not None and self.ci95_lower > self.ci95_upper:
            raise StrategyLabModelError("invalid confidence interval")
        if self.outcome is StrategyLabOutcome.SUPPORTED and (
            not has_metrics or self.ci95_lower is None or self.ci95_lower <= 0
        ):
            raise StrategyLabModelError("supported strategy lab result requires a positive interval")
        if self.outcome is StrategyLabOutcome.REFUTED and (
            not has_metrics or self.ci95_upper is None or self.ci95_upper >= 0
        ):
            raise StrategyLabModelError("refuted strategy lab result requires a negative interval")
        return self


class StrategyLabTraceNodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    lab_id: StrategyLabId
    iteration: int = Field(ge=1)
    parent_node_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    protocol_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: StrategyLabStatisticalResult
    feedback: StrategyLabAdaptation
    lifecycle_authority: Literal[False] = False
    allocation_authority: Literal[False] = False
    order_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_protocol_result_identity(self) -> Self:
        if self.result.protocol_id != self.protocol_id:
            raise StrategyLabModelError("strategy lab trace result protocol mismatch")
        return self


class StrategyLabTraceNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    node_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    body: StrategyLabTraceNodeBody


class StrategyLabCycle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    cycle_number: int = Field(ge=1)
    nodes: tuple[StrategyLabTraceNode, ...]
