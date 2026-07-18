from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.data_capability_models import (
    DataCapability,
    DataEntitlement,
    DataRequirementFailureMode,
    DataSourceId,
    StrategyDataRequirement,
)
from trading_agent.research_identity_models import StrategyLaneRef

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class DataRequirementStatus(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"


class StrategyDataStatus(StrEnum):
    READY = "ready"
    RESEARCH_ONLY = "research_only"
    BLOCKED_BY_DATA = "blocked_by_data"


class StrategyDataContractError(ValueError):
    pass


class DataSourceAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: DataSourceId
    satisfied: bool
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        reasons_valid = self.reason_codes == tuple(sorted(set(self.reason_codes))) and all(
            _REASON.fullmatch(reason) for reason in self.reason_codes
        )
        if not reasons_valid or self.satisfied is bool(self.reason_codes):
            raise StrategyDataContractError("invalid data source attempt")
        return self


class DataRequirementEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requirement_id: str
    status: DataRequirementStatus
    failure_mode: DataRequirementFailureMode
    selected_source_id: DataSourceId | None = None
    fallback_used: bool
    attempts: tuple[DataSourceAttempt, ...]

    @model_validator(mode="after")
    def validate_evaluation(self) -> Self:
        source_ids = tuple(attempt.source_id.canonical_id for attempt in self.attempts)
        satisfied_attempts = tuple(attempt for attempt in self.attempts if attempt.satisfied)
        selected_valid = (
            self.status is DataRequirementStatus.SATISFIED
            and self.selected_source_id is not None
            and len(satisfied_attempts) == 1
            and satisfied_attempts[0].source_id == self.selected_source_id
            and self.attempts[-1] == satisfied_attempts[0]
        )
        blocked_valid = (
            self.status is DataRequirementStatus.UNSATISFIED
            and self.selected_source_id is None
            and not satisfied_attempts
            and not self.fallback_used
        )
        fallback_valid = not self.fallback_used or (
            self.selected_source_id is not None and self.selected_source_id != self.attempts[0].source_id
        )
        if (
            _IDENTIFIER.fullmatch(self.requirement_id) is None
            or not self.attempts
            or len(source_ids) != len(set(source_ids))
            or not (selected_valid or blocked_valid)
            or not fallback_valid
        ):
            raise StrategyDataContractError("invalid data requirement evaluation")
        return self


class StrategyDataDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_lane: StrategyLaneRef
    evaluated_at: dt.datetime
    status: StrategyDataStatus
    evaluations: tuple[DataRequirementEvaluation, ...]

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        requirement_ids = tuple(evaluation.requirement_id for evaluation in self.evaluations)
        expected_status = _overall_status(self.evaluations)
        if (
            not _aware(self.evaluated_at)
            or not self.evaluations
            or requirement_ids != tuple(sorted(set(requirement_ids)))
            or self.status is not expected_status
        ):
            raise StrategyDataContractError("invalid strategy data decision")
        return self


class InvalidStrategyDataEvaluationError(ValueError):
    @override
    def __str__(self) -> str:
        return "전략 데이터 capability 평가 입력이 유효하지 않습니다"


def evaluate_strategy_data(
    requirements: Sequence[StrategyDataRequirement],
    capabilities: Sequence[DataCapability],
    entitlements: Sequence[DataEntitlement],
    *,
    evaluated_at: dt.datetime,
) -> StrategyDataDecision:
    from trading_agent.strategy_data_gate_evaluation import evaluate_requirement

    requirement_ids = tuple(requirement.requirement_id for requirement in requirements)
    lane_ids = tuple(requirement.strategy_lane.canonical_id for requirement in requirements)
    capability_ids = tuple(capability.source_id.canonical_id for capability in capabilities)
    entitlement_ids = tuple(entitlement.source_id.canonical_id for entitlement in entitlements)
    if (
        not _aware(evaluated_at)
        or not requirements
        or requirement_ids != tuple(sorted(set(requirement_ids)))
        or len(set(lane_ids)) != 1
        or len(capability_ids) != len(set(capability_ids))
        or len(entitlement_ids) != len(set(entitlement_ids))
    ):
        raise InvalidStrategyDataEvaluationError

    capability_by_source = {capability.source_id.canonical_id: capability for capability in capabilities}
    entitlement_by_source = {entitlement.source_id.canonical_id: entitlement for entitlement in entitlements}
    evaluations = tuple(
        evaluate_requirement(
            requirement,
            capability_by_source,
            entitlement_by_source,
            evaluated_at,
        )
        for requirement in requirements
    )
    return StrategyDataDecision(
        strategy_lane=requirements[0].strategy_lane,
        evaluated_at=evaluated_at,
        status=_overall_status(evaluations),
        evaluations=evaluations,
    )


def _overall_status(
    evaluations: Sequence[DataRequirementEvaluation],
) -> StrategyDataStatus:
    unresolved = tuple(
        evaluation for evaluation in evaluations if evaluation.status is DataRequirementStatus.UNSATISFIED
    )
    if not unresolved:
        return StrategyDataStatus.READY
    if any(evaluation.failure_mode is DataRequirementFailureMode.BLOCKED_BY_DATA for evaluation in unresolved):
        return StrategyDataStatus.BLOCKED_BY_DATA
    return StrategyDataStatus.RESEARCH_ONLY


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "DataRequirementEvaluation",
    "DataRequirementStatus",
    "DataSourceAttempt",
    "InvalidStrategyDataEvaluationError",
    "StrategyDataDecision",
    "StrategyDataStatus",
    "evaluate_strategy_data",
)
