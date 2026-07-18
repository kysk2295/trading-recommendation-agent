from __future__ import annotations

import datetime as dt
from typing import assert_never

from trading_agent.data_capability_models import (
    DataCapability,
    DataEntitlement,
    DataHealthState,
    DataSourceId,
    DataUse,
    StrategyDataRequirement,
)
from trading_agent.strategy_data_gate import (
    DataRequirementEvaluation,
    DataRequirementStatus,
    DataSourceAttempt,
)


def evaluate_requirement(
    requirement: StrategyDataRequirement,
    capabilities: dict[str, DataCapability],
    entitlements: dict[str, DataEntitlement],
    evaluated_at: dt.datetime,
) -> DataRequirementEvaluation:
    attempts: list[DataSourceAttempt] = []
    selected: DataSourceId | None = None
    for source_id in requirement.declared_source_ids:
        reasons = source_failure_reasons(
            requirement,
            capabilities.get(source_id.canonical_id),
            entitlements.get(source_id.canonical_id),
            evaluated_at,
        )
        attempts.append(
            DataSourceAttempt(
                source_id=source_id,
                satisfied=not reasons,
                reason_codes=reasons,
            )
        )
        if not reasons:
            selected = source_id
            break
    return DataRequirementEvaluation(
        requirement_id=requirement.requirement_id,
        status=DataRequirementStatus.SATISFIED if selected is not None else DataRequirementStatus.UNSATISFIED,
        failure_mode=requirement.failure_mode,
        selected_source_id=selected,
        fallback_used=selected is not None and selected != requirement.primary_source_id,
        attempts=tuple(attempts),
    )


def source_failure_reasons(
    requirement: StrategyDataRequirement,
    capability: DataCapability | None,
    entitlement: DataEntitlement | None,
    evaluated_at: dt.datetime,
) -> tuple[str, ...]:
    reasons: set[str] = set()
    if capability is None:
        reasons.add("capability_missing")
    else:
        check_capability(requirement, capability, evaluated_at, reasons)
    if entitlement is None:
        reasons.add("entitlement_missing")
    else:
        check_entitlement(requirement, entitlement, evaluated_at, reasons)
    return tuple(sorted(reasons))


def check_capability(
    requirement: StrategyDataRequirement,
    capability: DataCapability,
    evaluated_at: dt.datetime,
    reasons: set[str],
) -> None:
    if (
        capability.assessed_at > evaluated_at
        or (capability.latest_event_received_at is not None and capability.latest_event_received_at > evaluated_at)
        or (capability.latest_source_heartbeat_at is not None and capability.latest_source_heartbeat_at > evaluated_at)
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
    match capability.health_state:
        case DataHealthState.FAILED:
            reasons.add("capability_failed")
        case DataHealthState.INCOMPLETE:
            reasons.add("capability_incomplete")
        case DataHealthState.DEGRADED if not requirement.allow_degraded:
            reasons.add("degraded_not_allowed")
        case DataHealthState.COMPLETE | DataHealthState.DEGRADED:
            pass
        case _:
            assert_never(capability.health_state)
    freshness_values = tuple(
        value
        for value in (
            capability.latest_event_received_at,
            capability.latest_source_heartbeat_at,
        )
        if value is not None
    )
    latest = max(freshness_values) if freshness_values else None
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
        capability.historical_from is None or capability.historical_from > requirement.minimum_historical_start
    ):
        reasons.add("historical_depth_insufficient")


def check_entitlement(
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


__all__ = ("evaluate_requirement",)
