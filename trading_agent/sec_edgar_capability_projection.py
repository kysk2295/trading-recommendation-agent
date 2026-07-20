from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final, assert_never, override

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
from trading_agent.sec_edgar_capability_evidence import SecCapabilityEvidence
from trading_agent.sec_edgar_models import SecCollectionStatus
from trading_agent.security_master_models import DataMarketDomain

_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID: Final = DataSourceId(provider="sec", feed="edgar_submissions")
_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 20, tzinfo=dt.UTC)
_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=3_650,
    derived_retention_days=3_650,
    deletion_required=False,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)


class SecCapabilityProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR capability projection is invalid"


@dataclass(frozen=True, slots=True)
class SecCapabilityProjection:
    complete: bool
    declared_slice_count: int
    successful_slice_count: int
    failed_slice_count: int
    missing_slice_count: int
    filing_count: int
    capability: DataCapability
    entitlement: DataEntitlement


def project_sec_edgar_capability(
    evidence: SecCapabilityEvidence,
) -> SecCapabilityProjection:
    try:
        _require_valid_evidence(evidence)
        health = _health(evidence)
        completeness = evidence.successful_slice_count * 10_000 // evidence.declared_slice_count
        capability = DataCapability(
            source_id=_SOURCE_ID,
            source_class=DataSourceClass.REGULATORY_FUNDAMENTAL,
            market_domains=(DataMarketDomain.US_EQUITIES,),
            event_types=("filing_metadata",),
            universe="us_equities:bounded_issuer",
            delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
            historical_from=evidence.historical_from,
            expected_latency_ms=45_000,
            timestamp_semantics=(
                TimestampSemantic.PROVIDER_TIME,
                TimestampSemantic.RECEIVED_AT,
            ),
            retention=_RETENTION,
            rate_limits=DataRateLimits(requests_per_minute=600),
            freshness_slo_seconds=86_400,
            completeness_slo_bps=10_000,
            health_state=health,
            assessed_at=evidence.assessed_at,
            latest_event_received_at=evidence.latest_event_received_at,
            latest_source_heartbeat_at=evidence.latest_source_heartbeat_at,
            observed_completeness_bps=completeness,
        )
        entitlement = DataEntitlement(
            entitlement_id="sec-edgar-submissions-research-v1",
            source_id=_SOURCE_ID,
            market_domains=(DataMarketDomain.US_EQUITIES,),
            event_types=("filing_metadata",),
            permitted_uses=(DataUse.HISTORICAL_RESEARCH, DataUse.SHADOW_FORWARD),
            real_time=True,
            historical=True,
            redistribution_policy=RedistributionPolicy.DERIVED_ONLY,
            retention=_RETENTION,
            effective_from=_EFFECTIVE_FROM,
        )
        return SecCapabilityProjection(
            complete=health is DataHealthState.COMPLETE,
            declared_slice_count=evidence.declared_slice_count,
            successful_slice_count=evidence.successful_slice_count,
            failed_slice_count=evidence.failed_slice_count,
            missing_slice_count=evidence.missing_slice_count,
            filing_count=evidence.filing_count,
            capability=capability,
            entitlement=entitlement,
        )
    except (TypeError, ValidationError, ValueError, ZeroDivisionError):
        raise SecCapabilityProjectionError from None


def _require_valid_evidence(evidence: SecCapabilityEvidence) -> None:
    event_time_valid = (
        evidence.latest_event_received_at is None
        or (
            _aware(evidence.latest_event_received_at)
            and evidence.latest_event_received_at <= evidence.assessed_at
        )
    )
    counts = (
        evidence.successful_slice_count,
        evidence.failed_slice_count,
        evidence.missing_slice_count,
    )
    if (
        _HEX64.fullmatch(evidence.parent_run_id) is None
        or not _aware(evidence.assessed_at)
        or not _aware(evidence.latest_source_heartbeat_at)
        or evidence.latest_source_heartbeat_at != evidence.assessed_at
        or not event_time_valid
        or evidence.declared_slice_count < 1
        or any(value < 0 for value in counts)
        or sum(counts) != evidence.declared_slice_count
        or evidence.filing_count < 0
        or (evidence.filing_count == 0) != (evidence.historical_from is None)
        or (evidence.filing_count == 0) != (evidence.latest_event_received_at is None)
    ):
        raise SecCapabilityProjectionError
    match evidence.parent_status:
        case SecCollectionStatus.SUCCESS:
            if evidence.successful_slice_count < 1:
                raise SecCapabilityProjectionError
        case SecCollectionStatus.FAILED:
            if counts != (0, 1, 0) or evidence.declared_slice_count != 1:
                raise SecCapabilityProjectionError
        case unreachable:
            assert_never(unreachable)


def _health(evidence: SecCapabilityEvidence) -> DataHealthState:
    match evidence.parent_status:
        case SecCollectionStatus.FAILED:
            return DataHealthState.FAILED
        case SecCollectionStatus.SUCCESS:
            if evidence.failed_slice_count > 0:
                return DataHealthState.DEGRADED
            if evidence.missing_slice_count > 0:
                return DataHealthState.INCOMPLETE
            return DataHealthState.COMPLETE
        case unreachable:
            assert_never(unreachable)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "SecCapabilityProjection",
    "SecCapabilityProjectionError",
    "project_sec_edgar_capability",
)
