from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

from trading_agent.bls_public_models import (
    BlsPublicRequest,
    BlsPublicRun,
    BlsPublicStatus,
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

_SOURCE: Final = DataSourceId(provider="bls", feed="public_data_v1")
_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=3_650,
    derived_retention_days=3_650,
    deletion_required=False,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)
_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 24, tzinfo=dt.UTC)


class BlsPublicCapabilityError(ValueError):
    @override
    def __str__(self) -> str:
        return "BLS public data capability is invalid"


@dataclass(frozen=True, slots=True)
class BlsPublicCapabilityProjection:
    capability: DataCapability
    entitlement: DataEntitlement


def project_bls_public_capability(
    request: BlsPublicRequest,
    run: BlsPublicRun,
) -> BlsPublicCapabilityProjection:
    try:
        checked_request = BlsPublicRequest.model_validate(
            request.model_dump(mode="python")
        )
        checked_run = BlsPublicRun.model_validate(run.model_dump(mode="python"))
        if checked_run.request.request_id != checked_request.request_id:
            raise BlsPublicCapabilityError
        successful = checked_run.status is BlsPublicStatus.SUCCESS
        completeness = (
            checked_run.snapshot.observed_completeness_bps
            if checked_run.snapshot is not None
            else 0
        )
        latest = (
            checked_run.snapshot.observed_at
            if checked_run.snapshot is not None
            else None
        )
        capability = DataCapability(
            source_id=_SOURCE,
            source_class=DataSourceClass.MACRO_FLOW,
            market_domains=(DataMarketDomain.GLOBAL_MACRO,),
            event_types=("macro_observation",),
            universe="global_macro:bls_series",
            delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
            historical_from=(
                dt.date(checked_request.start_year, 1, 1)
                if successful
                else None
            ),
            expected_latency_ms=86_400_000,
            timestamp_semantics=(TimestampSemantic.RECEIVED_AT,),
            retention=_RETENTION,
            rate_limits=DataRateLimits(requests_per_minute=25),
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
            assessed_at=checked_run.completed_at,
            latest_event_received_at=latest,
            latest_source_heartbeat_at=checked_run.completed_at,
            observed_completeness_bps=completeness,
        )
        entitlement = DataEntitlement(
            entitlement_id="bls-public-data-v1-research",
            source_id=_SOURCE,
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
        return BlsPublicCapabilityProjection(capability, entitlement)
    except BlsPublicCapabilityError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise BlsPublicCapabilityError from None


__all__ = (
    "BlsPublicCapabilityError",
    "BlsPublicCapabilityProjection",
    "project_bls_public_capability",
)
