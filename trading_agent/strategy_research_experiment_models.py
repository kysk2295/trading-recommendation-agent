from __future__ import annotations

import datetime as dt
import math
from typing import Self, assert_never

from pydantic import Field, model_validator

from trading_agent.strategy_research_methodologies import ResamplingMethod
from trading_agent.strategy_research_results import TerminalResearchResult
from trading_agent.strategy_research_types import (
    AttemptStatus,
    CanonicalModel,
    ResearchAgentId,
    StrategyResearchContractError,
    aware,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ParameterValue(CanonicalModel):
    name: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)


class AttemptSpec(CanonicalModel):
    parameter_values: tuple[ParameterValue, ...]
    status: AttemptStatus
    train_values: tuple[float, ...]
    validation_values: tuple[float, ...]
    error_class: str | None
    elapsed_cpu_seconds: int = Field(ge=0, le=86_400)

    @model_validator(mode="after")
    def validate_spec(self) -> Self:
        names = tuple(item.name for item in self.parameter_values)
        if len(names) != len(set(names)) or not all(
            math.isfinite(value) for value in (*self.train_values, *self.validation_values)
        ):
            raise StrategyResearchContractError
        match self.status:
            case AttemptStatus.STARTED:
                raise StrategyResearchContractError
            case AttemptStatus.SUCCEEDED:
                if not self.train_values or not self.validation_values or self.error_class is not None:
                    raise StrategyResearchContractError
            case (
                AttemptStatus.FAILED
                | AttemptStatus.ABORTED
                | AttemptStatus.TIMED_OUT
                | AttemptStatus.CANCELLED
                | AttemptStatus.CENSORED
            ):
                if self.train_values or self.validation_values or not self.error_class:
                    raise StrategyResearchContractError
            case unreachable:
                assert_never(unreachable)
        return self


class ScienceExperiment(CanonicalModel):
    started_at: dt.datetime
    attempts: tuple[AttemptSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_experiment(self) -> Self:
        if not aware(self.started_at):
            raise StrategyResearchContractError
        return self


class FrozenScienceProtocol(CanonicalModel):
    protocol_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_sha256: str = Field(pattern=_SHA256_PATTERN)
    primary_metric: str = Field(min_length=1)
    baseline_id: str = Field(min_length=1)
    cost_model_id: str = Field(min_length=1)
    split_sha256: str = Field(pattern=_SHA256_PATTERN)
    falsification_rule: str = Field(min_length=1)
    max_parameter_combinations: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    max_cpu_seconds: int = Field(ge=1)
    minimum_observations: int = Field(ge=20)
    maximum_interval_width: float = Field(gt=0)
    bootstrap_repetitions: int = Field(ge=1_000)
    bootstrap_seed: int = Field(ge=0)
    familywise_alpha: float = Field(gt=0, lt=1)
    adjustment_tests: int = Field(ge=1)
    resampling_method: ResamplingMethod


class ScienceCycleResult(CanonicalModel):
    source_ids: tuple[str, ...] = Field(min_length=1)
    owner_agent_id: ResearchAgentId
    hypothesis_id: str = Field(min_length=1)
    protocol_id: str = Field(pattern=_SHA256_PATTERN)
    attempt_ids: tuple[str, ...] = Field(min_length=1)
    selected_attempt_id: str = Field(min_length=1)
    holdout_reveal_id: str = Field(min_length=1)
    terminal: TerminalResearchResult
    feedback_result_id: str = Field(min_length=1)


__all__ = (
    "AttemptSpec",
    "FrozenScienceProtocol",
    "ParameterValue",
    "ScienceCycleResult",
    "ScienceExperiment",
)
