from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

from trading_agent.alpaca_option_chain_models import (
    OptionChainRun,
    OptionChainStatus,
)
from trading_agent.data_capability_models import (
    DataCapability,
    DataCorrectionPolicy,
    DataDeliveryMode,
    DataEntitlement,
    DataHealthState,
    DataRateLimits,
    DataRetentionPolicy,
    DataSourceClass,
    DataSourceId,
    DataUse,
    RedistributionPolicy,
    TimestampSemantic,
)
from trading_agent.security_master_models import DataMarketDomain

_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 23, tzinfo=dt.UTC)
_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=7,
    derived_retention_days=30,
    deletion_required=True,
    correction_policy=DataCorrectionPolicy.SNAPSHOT_ONLY,
)


class AlpacaOptionChainCapabilityError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca option chain capability projection is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaOptionChainCapabilityProjection:
    complete: bool
    capability: DataCapability
    entitlement: DataEntitlement


def project_alpaca_option_chain_capability(
    run: OptionChainRun,
) -> AlpacaOptionChainCapabilityProjection:
    try:
        validated = OptionChainRun.model_validate(run.model_dump())
        complete = (
            validated.status is OptionChainStatus.SUCCESS
            and bool(validated.snapshots)
        )
        source = DataSourceId(
            provider="alpaca",
            feed=f"options_{validated.request.feed.value}",
        )
        capability = DataCapability(
            source_id=source,
            source_class=DataSourceClass.DERIVATIVES,
            market_domains=(DataMarketDomain.US_DERIVATIVES,),
            event_types=("option_chain_snapshot",),
            universe="us_derivatives:bounded_option_chain",
            delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
            historical_from=(
                validated.started_at.date() if complete else None
            ),
            expected_latency_ms=900_000,
            timestamp_semantics=(
                TimestampSemantic.PROVIDER_TIME,
                TimestampSemantic.RECEIVED_AT,
            ),
            retention=_RETENTION,
            rate_limits=DataRateLimits(requests_per_minute=200),
            freshness_slo_seconds=1_200,
            completeness_slo_bps=10_000,
            health_state=(
                DataHealthState.COMPLETE
                if complete
                else DataHealthState.FAILED
            ),
            assessed_at=validated.completed_at,
            latest_source_heartbeat_at=validated.completed_at,
            observed_completeness_bps=10_000 if complete else 0,
        )
        entitlement = DataEntitlement(
            entitlement_id=(
                f"alpaca-options-{validated.request.feed.value}-"
                "research-shadow-v1"
            ),
            source_id=source,
            market_domains=(DataMarketDomain.US_DERIVATIVES,),
            event_types=("option_chain_snapshot",),
            permitted_uses=(
                DataUse.HISTORICAL_RESEARCH,
                DataUse.SHADOW_FORWARD,
            ),
            real_time=False,
            historical=True,
            redistribution_policy=RedistributionPolicy.NONE,
            retention=_RETENTION,
            effective_from=_EFFECTIVE_FROM,
        )
        return AlpacaOptionChainCapabilityProjection(
            complete,
            capability,
            entitlement,
        )
    except AlpacaOptionChainCapabilityError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise AlpacaOptionChainCapabilityError from None


__all__ = (
    "AlpacaOptionChainCapabilityError",
    "AlpacaOptionChainCapabilityProjection",
    "project_alpaca_option_chain_capability",
)
