from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

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

EFFECTIVE_FROM = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
EFFECTIVE_TO = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
ASSESSED_AT = dt.datetime(2026, 7, 17, 14, tzinfo=dt.UTC)
LATEST_EVENT = ASSESSED_AT - dt.timedelta(seconds=1)


def test_data_source_has_stable_provider_feed_identity() -> None:
    source = _source()

    assert source.canonical_id == "fixture/sip"
    assert source.model_dump(mode="json") == {
        "schema_version": 1,
        "provider": "fixture",
        "feed": "sip",
    }
    with pytest.raises(ValidationError):
        DataSourceId(provider="Fixture", feed="sip")


def test_entitlement_records_use_retention_and_correction_without_credentials() -> None:
    entitlement = _entitlement()

    assert entitlement.permitted_uses == (
        DataUse.HISTORICAL_RESEARCH,
        DataUse.PAPER_RECOMMENDATION,
        DataUse.SHADOW_FORWARD,
    )
    assert entitlement.retention.raw_retention_days == 30
    payload = entitlement.model_dump(mode="python")
    payload["api_key"] = "forbidden"
    with pytest.raises(ValidationError):
        DataEntitlement.model_validate(payload)


@pytest.mark.parametrize(
    "override",
    (
        {"market_domains": (DataMarketDomain.US_EQUITIES, DataMarketDomain.US_EQUITIES)},
        {"event_types": ("trade", "minute_bar")},
        {"permitted_uses": (DataUse.SHADOW_FORWARD, DataUse.HISTORICAL_RESEARCH)},
        {"real_time": False, "historical": False},
        {"real_time": False},
        {"historical": False},
        {"effective_to": EFFECTIVE_FROM},
    ),
)
def test_entitlement_rejects_ambiguous_or_incompatible_authority(
    override: dict[str, object],
) -> None:
    payload = _entitlement().model_dump(mode="python")
    payload.update(override)

    with pytest.raises(ValidationError):
        DataEntitlement.model_validate(payload)


def test_rate_and_retention_limits_are_explicit() -> None:
    with pytest.raises(ValidationError):
        DataRateLimits()
    with pytest.raises(ValidationError):
        DataRateLimits(requests_per_minute=0)
    with pytest.raises(ValidationError):
        DataRetentionPolicy(
            raw_retention_days=31,
            derived_retention_days=30,
            deletion_required=True,
            correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
        )


def test_capability_records_current_quality_and_source_contract() -> None:
    capability = _capability()

    assert capability.health_state is DataHealthState.COMPLETE
    assert capability.event_types == ("minute_bar", "quote", "trade")
    assert capability.observed_completeness_bps == 10_000


@pytest.mark.parametrize(
    "override",
    (
        {"market_domains": (DataMarketDomain.US_EQUITIES, DataMarketDomain.US_EQUITIES)},
        {"event_types": ("trade", "minute_bar")},
        {"delivery_modes": (DataDeliveryMode.WEBSOCKET_STREAM, DataDeliveryMode.REST_SNAPSHOT)},
        {"timestamp_semantics": (TimestampSemantic.RECEIVED_AT, TimestampSemantic.EVENT_TIME)},
        {"expected_latency_ms": 5_001},
        {"latest_event_received_at": ASSESSED_AT + dt.timedelta(microseconds=1)},
        {"health_state": DataHealthState.COMPLETE, "observed_completeness_bps": 9_899},
        {"health_state": DataHealthState.DEGRADED, "latest_event_received_at": None},
        {"assessed_at": ASSESSED_AT.replace(tzinfo=None)},
    ),
)
def test_capability_rejects_noncanonical_or_unsupported_quality_snapshot(
    override: dict[str, object],
) -> None:
    payload = _capability().model_dump(mode="python")
    payload.update(override)

    with pytest.raises(ValidationError):
        DataCapability.model_validate(payload)


def test_strategy_requirement_preserves_declared_fallback_priority() -> None:
    requirement = _requirement(
        fallback_source_ids=(
            DataSourceId(provider="z_backup", feed="sip"),
            DataSourceId(provider="a_backup", feed="sip"),
        )
    )

    assert tuple(source.provider for source in requirement.fallback_source_ids) == ("z_backup", "a_backup")
    assert requirement.strategy_lane.canonical_id == "us_equities/day_trading/orb"


@pytest.mark.parametrize(
    "override",
    (
        {"fallback_source_ids": (DataSourceId(provider="fixture", feed="sip"),)},
        {
            "fallback_source_ids": (
                DataSourceId(provider="backup", feed="sip"),
                DataSourceId(provider="backup", feed="sip"),
            )
        },
        {"required_timestamp_semantics": (TimestampSemantic.RECEIVED_AT, TimestampSemantic.EVENT_TIME)},
        {"required_delivery_modes": ()},
        {"max_age_seconds": 0},
        {"minimum_completeness_bps": 10_001},
        {"data_use": DataUse.HISTORICAL_RESEARCH, "minimum_historical_start": None},
    ),
)
def test_strategy_requirement_rejects_implicit_or_invalid_source_contract(
    override: dict[str, object],
) -> None:
    payload = _requirement().model_dump(mode="python")
    payload.update(override)

    with pytest.raises(ValidationError):
        StrategyDataRequirement.model_validate(payload)


def _source() -> DataSourceId:
    return DataSourceId(provider="fixture", feed="sip")


def _retention() -> DataRetentionPolicy:
    return DataRetentionPolicy(
        raw_retention_days=30,
        derived_retention_days=365,
        deletion_required=True,
        correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
    )


def _entitlement() -> DataEntitlement:
    return DataEntitlement(
        entitlement_id="fixture-sip-research-v1",
        source_id=_source(),
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
        effective_to=EFFECTIVE_TO,
    )


def _capability() -> DataCapability:
    return DataCapability(
        source_id=_source(),
        source_class=DataSourceClass.MARKET_MICROSTRUCTURE,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=("minute_bar", "quote", "trade"),
        universe="us_equities:all_active",
        delivery_modes=(DataDeliveryMode.REST_SNAPSHOT, DataDeliveryMode.WEBSOCKET_STREAM),
        historical_from=dt.date(2020, 1, 1),
        expected_latency_ms=250,
        timestamp_semantics=(
            TimestampSemantic.EVENT_TIME,
            TimestampSemantic.PROVIDER_TIME,
            TimestampSemantic.RECEIVED_AT,
        ),
        retention=_retention(),
        rate_limits=DataRateLimits(
            requests_per_minute=200,
            max_connections=2,
            max_subscriptions=30,
        ),
        freshness_slo_seconds=5,
        completeness_slo_bps=9_900,
        health_state=DataHealthState.COMPLETE,
        assessed_at=ASSESSED_AT,
        latest_event_received_at=LATEST_EVENT,
        observed_completeness_bps=10_000,
    )


def _requirement(
    *,
    fallback_source_ids: tuple[DataSourceId, ...] = (),
) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        requirement_id="orb-minute-bar-current",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id="orb",
        ),
        data_use=DataUse.PAPER_RECOMMENDATION,
        market_domain=DataMarketDomain.US_EQUITIES,
        event_type="minute_bar",
        primary_source_id=_source(),
        fallback_source_ids=fallback_source_ids,
        required_delivery_modes=(DataDeliveryMode.WEBSOCKET_STREAM,),
        required_timestamp_semantics=(TimestampSemantic.EVENT_TIME, TimestampSemantic.RECEIVED_AT),
        max_age_seconds=5,
        minimum_completeness_bps=9_900,
        minimum_historical_start=None,
        allow_degraded=False,
        failure_mode=DataRequirementFailureMode.BLOCKED_BY_DATA,
    )
