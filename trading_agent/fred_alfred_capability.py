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
from trading_agent.fred_alfred_models import FredRunStatus, FredSourceMode
from trading_agent.fred_alfred_snapshot_models import FredAlfredTerminal
from trading_agent.security_master_models import DataMarketDomain

_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=3_650,
    derived_retention_days=3_650,
    deletion_required=False,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)
_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 24, tzinfo=dt.UTC)


class FredCapabilityError(ValueError):
    @override
    def __str__(self) -> str:
        return "FRED/ALFRED capability is invalid"


@dataclass(frozen=True, slots=True)
class FredCapabilityProjection:
    capability: DataCapability
    entitlement: DataEntitlement


def project_fred_capability(
    terminal: FredAlfredTerminal,
) -> FredCapabilityProjection:
    try:
        checked = FredAlfredTerminal.model_validate(
            terminal.model_dump(mode="python")
        )
        source = _source(checked.request.source_mode)
        snapshot = checked.snapshot
        successful = checked.status is FredRunStatus.SUCCESS
        completeness = (
            snapshot.observed_completeness_bps if snapshot is not None else 0
        )
        capability = DataCapability(
            source_id=source,
            source_class=DataSourceClass.MACRO_FLOW,
            market_domains=(DataMarketDomain.GLOBAL_MACRO,),
            event_types=("macro_observation",),
            universe=f"global_macro:{checked.request.source_mode.value}_series",
            delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
            historical_from=(
                min(item.observation_date for item in snapshot.observations)
                if snapshot is not None
                else None
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
                if successful and completeness == 10_000
                else (
                    DataHealthState.DEGRADED
                    if successful
                    else DataHealthState.FAILED
                )
            ),
            assessed_at=checked.completed_at,
            latest_event_received_at=(
                snapshot.observed_at if snapshot is not None else None
            ),
            latest_source_heartbeat_at=checked.completed_at,
            observed_completeness_bps=completeness,
        )
        entitlement = DataEntitlement(
            entitlement_id=(
                f"{checked.request.source_mode.value}-observations-v1-research"
            ),
            source_id=source,
            market_domains=(DataMarketDomain.GLOBAL_MACRO,),
            event_types=("macro_observation",),
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
        return FredCapabilityProjection(capability, entitlement)
    except (TypeError, ValidationError, ValueError):
        raise FredCapabilityError from None


def _source(mode: FredSourceMode) -> DataSourceId:
    return DataSourceId(
        provider=mode.value,
        feed=(
            "series_observations"
            if mode is FredSourceMode.FRED
            else "vintage_observations"
        ),
    )


__all__ = (
    "FredCapabilityError",
    "FredCapabilityProjection",
    "project_fred_capability",
)
