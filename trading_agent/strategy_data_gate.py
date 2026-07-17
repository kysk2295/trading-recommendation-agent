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
    DataHealthState,
    DataRequirementFailureMode,
    DataSourceId,
    DataUse,
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


class DataSourceAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: DataSourceId
    satisfied: bool
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        reasons_valid = (
            self.reason_codes == tuple(sorted(set(self.reason_codes)))
            and all(_REASON.fullmatch(reason) for reason in self.reason_codes)
        )
        if not reasons_valid or self.satisfied is bool(self.reason_codes):
            raise ValueError("invalid data source attempt")
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
            raise ValueError("invalid data requirement evaluation")
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
            raise ValueError("invalid strategy data decision")
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

    capability_by_source = {
        capability.source_id.canonical_id: capability
        for capability in capabilities
    }
    entitlement_by_source = {
        entitlement.source_id.canonical_id: entitlement
        for entitlement in entitlements
    }
    evaluations = tuple(
        _evaluate_requirement(
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


def _evaluate_requirement(
    requirement: StrategyDataRequirement,
    capabilities: dict[str, DataCapability],
    entitlements: dict[str, DataEntitlement],
    evaluated_at: dt.datetime,
) -> DataRequirementEvaluation:
    attempts: list[DataSourceAttempt] = []
    selected: DataSourceId | None = None
    for source_id in requirement.declared_source_ids:
        reasons = _source_failure_reasons(
            requirement,
            capabilities.get(source_id.canonical_id),
            entitlements.get(source_id.canonical_id),
            evaluated_at,
        )
        attempt = DataSourceAttempt(
            source_id=source_id,
            satisfied=not reasons,
            reason_codes=reasons,
        )
        attempts.append(attempt)
        if not reasons:
            selected = source_id
            break
    return DataRequirementEvaluation(
        requirement_id=requirement.requirement_id,
        status=(
            DataRequirementStatus.SATISFIED
            if selected is not None
            else DataRequirementStatus.UNSATISFIED
        ),
        failure_mode=requirement.failure_mode,
        selected_source_id=selected,
        fallback_used=selected is not None and selected != requirement.primary_source_id,
        attempts=tuple(attempts),
    )


def _source_failure_reasons(
    requirement: StrategyDataRequirement,
    capability: DataCapability | None,
    entitlement: DataEntitlement | None,
    evaluated_at: dt.datetime,
) -> tuple[str, ...]:
    reasons: set[str] = set()
    if capability is None:
        reasons.add("capability_missing")
    else:
        _check_capability(requirement, capability, evaluated_at, reasons)
    if entitlement is None:
        reasons.add("entitlement_missing")
    else:
        _check_entitlement(requirement, entitlement, evaluated_at, reasons)
    return tuple(sorted(reasons))


def _check_capability(
    requirement: StrategyDataRequirement,
    capability: DataCapability,
    evaluated_at: dt.datetime,
    reasons: set[str],
) -> None:
    if capability.assessed_at > evaluated_at or (
        capability.latest_event_received_at is not None
        and capability.latest_event_received_at > evaluated_at
    ):
        reasons.add("capability_from_future")
    if requirement.market_domain not in capability.market_domains:
        reasons.add("market_domain_not_supported")
    if requirement.event_type not in capability.event_types:
        reasons.add("event_type_not_supported")
    if not set(requirement.required_delivery_modes).issubset(capability.delivery_modes):
        reasons.add("delivery_mode_not_supported")
    if not set(requirement.required_timestamp_semantics).issubset(capability.timestamp_semantics):
        reasons.add("timestamp_semantic_not_supported")
    if capability.health_state is DataHealthState.FAILED:
        reasons.add("capability_failed")
    elif capability.health_state is DataHealthState.INCOMPLETE:
        reasons.add("capability_incomplete")
    elif capability.health_state is DataHealthState.DEGRADED and not requirement.allow_degraded:
        reasons.add("degraded_not_allowed")
    latest = capability.latest_event_received_at
    if latest is None:
        reasons.add("latest_event_missing")
    elif latest <= evaluated_at and evaluated_at - latest > dt.timedelta(seconds=requirement.max_age_seconds):
        reasons.add("capability_stale")
    if (
        capability.freshness_slo_seconds > requirement.max_age_seconds
        or capability.expected_latency_ms > requirement.max_age_seconds * 1_000
    ):
        reasons.add("freshness_slo_exceeds_requirement")
    if capability.observed_completeness_bps < requirement.minimum_completeness_bps:
        reasons.add("completeness_below_requirement")
    if requirement.minimum_historical_start is not None and (
        capability.historical_from is None
        or capability.historical_from > requirement.minimum_historical_start
    ):
        reasons.add("historical_depth_insufficient")


def _check_entitlement(
    requirement: StrategyDataRequirement,
    entitlement: DataEntitlement,
    evaluated_at: dt.datetime,
    reasons: set[str],
) -> None:
    if not (
        entitlement.effective_from <= evaluated_at
        and (entitlement.effective_to is None or evaluated_at < entitlement.effective_to)
    ):
        reasons.add("entitlement_inactive")
    if requirement.data_use not in entitlement.permitted_uses:
        reasons.add("entitlement_use_not_allowed")
    if requirement.market_domain not in entitlement.market_domains:
        reasons.add("entitlement_domain_not_allowed")
    if requirement.event_type not in entitlement.event_types:
        reasons.add("entitlement_event_not_allowed")
    if requirement.data_use is DataUse.PAPER_RECOMMENDATION and not entitlement.real_time:
        reasons.add("realtime_not_entitled")
    if requirement.data_use is DataUse.HISTORICAL_RESEARCH and not entitlement.historical:
        reasons.add("historical_not_entitled")


def _overall_status(
    evaluations: Sequence[DataRequirementEvaluation],
) -> StrategyDataStatus:
    unresolved = tuple(
        evaluation
        for evaluation in evaluations
        if evaluation.status is DataRequirementStatus.UNSATISFIED
    )
    if not unresolved:
        return StrategyDataStatus.READY
    if any(
        evaluation.failure_mode is DataRequirementFailureMode.BLOCKED_BY_DATA
        for evaluation in unresolved
    ):
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
