from __future__ import annotations

import datetime as dt

from trading_agent.data_capability_models import (
    DataCapability,
    DataDeliveryMode,
    DataEntitlement,
    DataHealthState,
    DataRateLimits,
    DataRequirementFailureMode,
    DataSourceClass,
    DataUse,
    StrategyDataRequirement,
    TimestampSemantic,
)
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.intraday_research_dataset_models import IntradayResearchDatasetReceipt
from trading_agent.intraday_research_input_binding_models import (
    IntradayResearchInputBindingError,
)
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.security_master_models import DataMarketDomain
from trading_agent.strategy_data_gate import StrategyDataStatus
from trading_agent.strategy_factory import StrategyMode


def build_actual_intraday_data_foundation(
    strategy: StrategyMode,
    evaluated_at: dt.datetime,
    receipt: IntradayResearchDatasetReceipt,
    entitlement: DataEntitlement,
) -> DataFoundationManifest:
    source_id = entitlement.source_id
    lane = StrategyLaneRef(
        market_id=MarketId.US_EQUITIES,
        agent_family=AgentFamily.DAY_TRADING,
        strategy_id=strategy.value,
    )
    capability = DataCapability(
        source_id=source_id,
        source_class=DataSourceClass.MARKET_MICROSTRUCTURE,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=("minute_bar",),
        universe="us_equities:strict_forward_sessions",
        delivery_modes=(DataDeliveryMode.FILE_BATCH, DataDeliveryMode.LOCAL_DERIVED),
        historical_from=min(receipt.session_dates),
        expected_latency_ms=0,
        timestamp_semantics=(TimestampSemantic.EVENT_TIME,),
        retention=entitlement.retention,
        rate_limits=DataRateLimits(requests_per_minute=1),
        freshness_slo_seconds=86_400,
        completeness_slo_bps=10_000,
        health_state=DataHealthState.COMPLETE,
        assessed_at=evaluated_at,
        latest_source_heartbeat_at=evaluated_at,
        observed_completeness_bps=10_000,
    )
    requirement = StrategyDataRequirement(
        requirement_id=f"{strategy.value}-strict-forward-minute-history",
        strategy_lane=lane,
        data_use=DataUse.HISTORICAL_RESEARCH,
        market_domain=DataMarketDomain.US_EQUITIES,
        event_type="minute_bar",
        primary_source_id=source_id,
        required_delivery_modes=(DataDeliveryMode.FILE_BATCH,),
        required_timestamp_semantics=(TimestampSemantic.EVENT_TIME,),
        max_age_seconds=86_400,
        minimum_completeness_bps=10_000,
        minimum_historical_start=min(receipt.session_dates),
        allow_degraded=False,
        failure_mode=DataRequirementFailureMode.BLOCKED_BY_DATA,
    )
    foundation = DataFoundationManifest(
        manifest_id=f"{strategy.value}-strict-forward-{receipt.input_sha256[:16]}",
        registered_at=evaluated_at,
        evaluated_at=evaluated_at,
        strategy_lane=lane,
        capabilities=(capability,),
        entitlements=(entitlement,),
        requirements=(requirement,),
    )
    if foundation.evaluate_data_readiness().status is not StrategyDataStatus.READY:
        raise IntradayResearchInputBindingError("foundation_not_ready")
    return foundation


__all__ = ("build_actual_intraday_data_foundation",)
