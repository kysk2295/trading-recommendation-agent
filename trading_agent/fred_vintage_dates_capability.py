from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

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
from trading_agent.fred_alfred_models import FredRunStatus
from trading_agent.fred_vintage_dates_models import FredVintageDatesTerminal
from trading_agent.security_master_models import DataMarketDomain

_SOURCE: Final = DataSourceId(provider="fred", feed="series_vintage_dates")
_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=3_650,
    derived_retention_days=3_650,
    deletion_required=False,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)
_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 24, tzinfo=dt.UTC)


class FredVintageDatesCapabilityError(ValueError):
    @override
    def __str__(self) -> str:
        return "FRED vintage dates capability is invalid"


@dataclass(frozen=True, slots=True)
class FredVintageDatesCapabilityProjection:
    capability: DataCapability
    entitlement: DataEntitlement


def project_fred_vintage_dates_capability(
    terminal: FredVintageDatesTerminal,
) -> FredVintageDatesCapabilityProjection:
    try:
        checked = FredVintageDatesTerminal.model_validate(
            terminal.model_dump(mode="python")
        )
        snapshot = checked.snapshot
        successful = checked.status is FredRunStatus.SUCCESS
        capability = DataCapability(
            source_id=_SOURCE,
            source_class=DataSourceClass.MACRO_FLOW,
            market_domains=(DataMarketDomain.GLOBAL_MACRO,),
            event_types=("macro_release_date",),
            universe="global_macro:fred_series",
            delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
            historical_from=(
                min(snapshot.vintage_dates) if snapshot is not None else None
            ),
            expected_latency_ms=86_400_000,
            timestamp_semantics=(
                TimestampSemantic.EVENT_TIME,
                TimestampSemantic.RECEIVED_AT,
            ),
            retention=_RETENTION,
            rate_limits=DataRateLimits(requests_per_minute=30),
            freshness_slo_seconds=86_400,
            completeness_slo_bps=10_000,
            health_state=(
                DataHealthState.COMPLETE
                if successful
                else DataHealthState.FAILED
            ),
            assessed_at=checked.completed_at,
            latest_event_received_at=(
                snapshot.observed_at if snapshot is not None else None
            ),
            latest_source_heartbeat_at=checked.completed_at,
            observed_completeness_bps=10_000 if successful else 0,
        )
        entitlement = DataEntitlement(
            entitlement_id="fred-series-vintage-dates-v1-research",
            source_id=_SOURCE,
            market_domains=(DataMarketDomain.GLOBAL_MACRO,),
            event_types=("macro_release_date",),
            permitted_uses=(
                DataUse.HISTORICAL_RESEARCH,
                DataUse.SHADOW_FORWARD,
            ),
            real_time=False,
            historical=True,
            redistribution_policy=RedistributionPolicy.ATTRIBUTED_SUMMARY,
            retention=_RETENTION,
            effective_from=_EFFECTIVE_FROM,
        )
        return FredVintageDatesCapabilityProjection(capability, entitlement)
    except (TypeError, ValidationError, ValueError):
        raise FredVintageDatesCapabilityError from None


__all__ = (
    "FredVintageDatesCapabilityError",
    "FredVintageDatesCapabilityProjection",
    "project_fred_vintage_dates_capability",
)
