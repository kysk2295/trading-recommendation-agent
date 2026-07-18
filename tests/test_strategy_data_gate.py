from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.data_capability_models import (
    DataCapability,
    DataCorrectionPolicy,
    DataDeliveryMode,
    DataEntitlement,
    DataHealthState,
    DataRateLimits,
    DataRequirementFailureMode,
    DataRetentionPolicy,
    DataSourceClass,
    DataSourceId,
    DataUse,
    RedistributionPolicy,
    StrategyDataRequirement,
    TimestampSemantic,
)
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.security_master_models import DataMarketDomain
from trading_agent.strategy_data_gate import (
    DataRequirementStatus,
    InvalidStrategyDataEvaluationError,
    StrategyDataStatus,
    evaluate_strategy_data,
)

EVALUATED_AT = dt.datetime(2026, 7, 17, 14, tzinfo=dt.UTC)
EFFECTIVE_FROM = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
EFFECTIVE_TO = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)


def test_matching_primary_capability_is_ready() -> None:
    decision = _evaluate()

    evaluation = decision.evaluations[0]
    assert decision.status is StrategyDataStatus.READY
    assert evaluation.status is DataRequirementStatus.SATISFIED
    assert evaluation.selected_source_id == _source()
    assert evaluation.fallback_used is False
    assert evaluation.attempts[0].satisfied is True
    assert evaluation.attempts[0].reason_codes == ()


def test_undeclared_provider_is_never_selected() -> None:
    rogue = _source("rogue")
    decision = _evaluate(
        capabilities=(_capability(rogue),),
        entitlements=(_entitlement(rogue),),
    )

    evaluation = decision.evaluations[0]
    assert decision.status is StrategyDataStatus.BLOCKED_BY_DATA
    assert evaluation.selected_source_id is None
    assert tuple(attempt.source_id for attempt in evaluation.attempts) == (_source(),)
    assert evaluation.attempts[0].reason_codes == ("capability_missing", "entitlement_missing")


def test_declared_fallback_is_selected_after_audited_primary_failure() -> None:
    backup = _source("backup")
    requirement = _requirement(fallback_source_ids=(backup,))
    decision = _evaluate(
        requirements=(requirement,),
        capabilities=(
            _capability(health_state=DataHealthState.FAILED),
            _capability(backup),
        ),
        entitlements=(_entitlement(), _entitlement(backup)),
    )

    evaluation = decision.evaluations[0]
    assert decision.status is StrategyDataStatus.READY
    assert evaluation.selected_source_id == backup
    assert evaluation.fallback_used is True
    assert evaluation.attempts[0].reason_codes == ("capability_failed",)
    assert evaluation.attempts[1].satisfied is True


def test_fresh_sparse_source_heartbeat_satisfies_freshness_without_event() -> None:
    capability = _capability(
        latest_event_received_at=None,
        latest_source_heartbeat_at=EVALUATED_AT - dt.timedelta(seconds=1),
    )

    decision = _evaluate(capabilities=(capability,), entitlements=(_entitlement(),))

    assert decision.status is StrategyDataStatus.READY


@pytest.mark.parametrize(
    ("case", "reason"),
    (
        ("missing_entitlement", "entitlement_missing"),
        ("expired_entitlement", "entitlement_inactive"),
        ("stale", "capability_stale"),
        ("incomplete", "completeness_below_requirement"),
        ("timestamp", "timestamp_semantic_not_supported"),
        ("delivery", "delivery_mode_not_supported"),
        ("failed", "capability_failed"),
        ("future", "capability_from_future"),
    ),
)
def test_gate_emits_fixed_failure_reasons(case: str, reason: str) -> None:
    capability = _capability()
    entitlement: DataEntitlement | None = _entitlement()
    if case == "missing_entitlement":
        entitlement = None
    elif case == "expired_entitlement":
        entitlement = _entitlement(effective_to=EVALUATED_AT)
    elif case == "stale":
        capability = _capability(latest_event_received_at=EVALUATED_AT - dt.timedelta(seconds=6))
    elif case == "incomplete":
        capability = _capability(completeness_slo_bps=9_000, observed_completeness_bps=9_500)
    elif case == "timestamp":
        capability = _capability(timestamp_semantics=(TimestampSemantic.RECEIVED_AT,))
    elif case == "delivery":
        capability = _capability(delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,))
    elif case == "failed":
        capability = _capability(health_state=DataHealthState.FAILED)
    elif case == "future":
        future = EVALUATED_AT + dt.timedelta(seconds=1)
        capability = _capability(assessed_at=future, latest_event_received_at=future)

    decision = _evaluate(
        capabilities=(capability,),
        entitlements=() if entitlement is None else (entitlement,),
    )

    assert decision.status is StrategyDataStatus.BLOCKED_BY_DATA
    assert reason in decision.evaluations[0].attempts[0].reason_codes


def test_historical_requirement_checks_entitlement_and_depth() -> None:
    requirement = _requirement(
        data_use=DataUse.HISTORICAL_RESEARCH,
        minimum_historical_start=dt.date(2019, 1, 1),
    )
    decision = _evaluate(requirements=(requirement,))

    assert decision.status is StrategyDataStatus.BLOCKED_BY_DATA
    assert decision.evaluations[0].attempts[0].reason_codes == ("historical_depth_insufficient",)


def test_degraded_capability_requires_explicit_strategy_permission() -> None:
    degraded = _capability(health_state=DataHealthState.DEGRADED)

    blocked = _evaluate(capabilities=(degraded,))
    allowed = _evaluate(
        requirements=(_requirement(allow_degraded=True),),
        capabilities=(degraded,),
    )

    assert blocked.status is StrategyDataStatus.BLOCKED_BY_DATA
    assert blocked.evaluations[0].attempts[0].reason_codes == ("degraded_not_allowed",)
    assert allowed.status is StrategyDataStatus.READY


def test_soft_unresolved_requirements_close_as_research_only() -> None:
    decision = _evaluate(
        requirements=(_requirement(failure_mode=DataRequirementFailureMode.RESEARCH_ONLY),),
        capabilities=(),
        entitlements=(),
    )

    assert decision.status is StrategyDataStatus.RESEARCH_ONLY


def test_hard_blocking_dominates_soft_research_only() -> None:
    decision = _evaluate(
        requirements=(
            _requirement(requirement_id="a-hard"),
            _requirement(
                requirement_id="b-soft",
                event_type="quote",
                failure_mode=DataRequirementFailureMode.RESEARCH_ONLY,
            ),
        ),
        capabilities=(),
        entitlements=(),
    )

    assert decision.status is StrategyDataStatus.BLOCKED_BY_DATA


def test_gate_rejects_duplicate_noncanonical_or_mixed_lane_requirements() -> None:
    requirement = _requirement()
    mixed_lane = _requirement(
        requirement_id="swing-minute-bar-current",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.SWING_TRADING,
            strategy_id="new_high_rvol",
        ),
    )

    with pytest.raises(InvalidStrategyDataEvaluationError):
        _evaluate(requirements=(requirement, requirement))
    with pytest.raises(InvalidStrategyDataEvaluationError):
        _evaluate(requirements=(mixed_lane, requirement))
    with pytest.raises(InvalidStrategyDataEvaluationError):
        evaluate_strategy_data(
            (requirement,),
            (_capability(),),
            (_entitlement(),),
            evaluated_at=EVALUATED_AT.replace(tzinfo=None),
        )


def _evaluate(
    *,
    requirements: tuple[StrategyDataRequirement, ...] | None = None,
    capabilities: tuple[DataCapability, ...] | None = None,
    entitlements: tuple[DataEntitlement, ...] | None = None,
):
    return evaluate_strategy_data(
        requirements or (_requirement(),),
        (_capability(),) if capabilities is None else capabilities,
        (_entitlement(),) if entitlements is None else entitlements,
        evaluated_at=EVALUATED_AT,
    )


def _source(provider: str = "fixture") -> DataSourceId:
    return DataSourceId(provider=provider, feed="sip")


def _retention() -> DataRetentionPolicy:
    return DataRetentionPolicy(
        raw_retention_days=30,
        derived_retention_days=365,
        deletion_required=True,
        correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
    )


def _entitlement(
    source: DataSourceId | None = None,
    *,
    effective_to: dt.datetime = EFFECTIVE_TO,
) -> DataEntitlement:
    source = source or _source()
    return DataEntitlement(
        entitlement_id=f"{source.provider}-sip-research-v1",
        source_id=source,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=("minute_bar", "quote", "trade"),
        permitted_uses=(
            DataUse.HISTORICAL_RESEARCH,
            DataUse.PAPER_RECOMMENDATION,
            DataUse.SHADOW_FORWARD,
        ),
        real_time=True,
        historical=True,
        redistribution_policy=RedistributionPolicy.DERIVED_ONLY,
        retention=_retention(),
        effective_from=EFFECTIVE_FROM,
        effective_to=effective_to,
    )


def _capability(
    source: DataSourceId | None = None,
    **overrides: object,
) -> DataCapability:
    payload: dict[str, object] = {
        "source_id": source or _source(),
        "source_class": DataSourceClass.MARKET_MICROSTRUCTURE,
        "market_domains": (DataMarketDomain.US_EQUITIES,),
        "event_types": ("minute_bar", "quote", "trade"),
        "universe": "us_equities:all_active",
        "delivery_modes": (DataDeliveryMode.REST_SNAPSHOT, DataDeliveryMode.WEBSOCKET_STREAM),
        "historical_from": dt.date(2020, 1, 1),
        "expected_latency_ms": 250,
        "timestamp_semantics": (
            TimestampSemantic.EVENT_TIME,
            TimestampSemantic.PROVIDER_TIME,
            TimestampSemantic.RECEIVED_AT,
        ),
        "retention": _retention(),
        "rate_limits": DataRateLimits(
            requests_per_minute=200,
            max_connections=2,
            max_subscriptions=30,
        ),
        "freshness_slo_seconds": 5,
        "completeness_slo_bps": 9_900,
        "health_state": DataHealthState.COMPLETE,
        "assessed_at": EVALUATED_AT,
        "latest_event_received_at": EVALUATED_AT - dt.timedelta(seconds=1),
        "observed_completeness_bps": 10_000,
    }
    payload.update(overrides)
    return DataCapability.model_validate(payload)


def _requirement(
    *,
    requirement_id: str = "orb-minute-bar-current",
    strategy_lane: StrategyLaneRef | None = None,
    data_use: DataUse = DataUse.PAPER_RECOMMENDATION,
    event_type: str = "minute_bar",
    fallback_source_ids: tuple[DataSourceId, ...] = (),
    minimum_historical_start: dt.date | None = None,
    allow_degraded: bool = False,
    failure_mode: DataRequirementFailureMode = DataRequirementFailureMode.BLOCKED_BY_DATA,
) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        requirement_id=requirement_id,
        strategy_lane=strategy_lane
        or StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        data_use=data_use,
        market_domain=DataMarketDomain.US_EQUITIES,
        event_type=event_type,
        primary_source_id=_source(),
        fallback_source_ids=fallback_source_ids,
        required_delivery_modes=(DataDeliveryMode.WEBSOCKET_STREAM,),
        required_timestamp_semantics=(TimestampSemantic.EVENT_TIME, TimestampSemantic.RECEIVED_AT),
        max_age_seconds=5,
        minimum_completeness_bps=9_900,
        minimum_historical_start=minimum_historical_start,
        allow_degraded=allow_degraded,
        failure_mode=failure_mode,
    )
