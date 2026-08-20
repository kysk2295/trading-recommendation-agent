from __future__ import annotations

import math
from statistics import fmean
from typing import Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentEvaluationMetricsError(ValueError):
    @override
    def __str__(self) -> str:
        return "agent_evaluation_metrics_invalid"


class AgentEvaluationMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    theme_timing: float = Field(ge=0.0, le=1.0)
    leader_rank: float = Field(ge=0.0, le=1.0)
    recommendation_calibration: float = Field(ge=0.0, le=1.0)
    mfe: float = Field(ge=0.0, le=1.0)
    mae: float = Field(ge=-1.0, le=0.0)
    cost_adjusted_modeled_result: float = Field(ge=-1.0, le=1.0)
    no_trade_quality: float = Field(ge=0.0, le=1.0)
    evidence_fidelity: float = Field(ge=0.0, le=1.0)
    provenance_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        values = (
            self.theme_timing,
            self.leader_rank,
            self.recommendation_calibration,
            self.mfe,
            self.mae,
            self.cost_adjusted_modeled_result,
            self.no_trade_quality,
            self.evidence_fidelity,
        )
        if (
            not all(math.isfinite(item) for item in values)
            or self.provenance_ids != tuple(sorted(set(self.provenance_ids)))
            or any(not item.strip() for item in self.provenance_ids)
        ):
            raise AgentEvaluationMetricsError
        return self

    @property
    def aggregate_score(self) -> float:
        return fmean(
            (
                self.theme_timing,
                self.leader_rank,
                self.recommendation_calibration,
                self.mfe,
                1.0 + self.mae,
                (1.0 + self.cost_adjusted_modeled_result) / 2.0,
                self.no_trade_quality,
                self.evidence_fidelity,
            )
        )


class AgentScoreComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    champion: AgentEvaluationMetrics
    challenger: AgentEvaluationMetrics

    @property
    def champion_score(self) -> float:
        return self.champion.aggregate_score

    @property
    def challenger_score(self) -> float:
        return self.challenger.aggregate_score

    @property
    def margin(self) -> float:
        return self.challenger_score - self.champion_score


__all__ = ("AgentEvaluationMetrics", "AgentScoreComparison")
