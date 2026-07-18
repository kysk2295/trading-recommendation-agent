from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final, override

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
from trading_agent.us_market_data_fleet import RuntimeFleetStatus, RuntimeOwnerStatus
from trading_agent.us_market_data_fleet_audit import (
    RuntimeFleetAuditRecord,
    RuntimeOwnerAudit,
    validate_runtime_fleet_audit,
)
from trading_agent.us_market_data_runtime_models import MarketDataRuntimeStatus
from trading_agent.us_subscription_models import SubscriptionPolicyStatus

_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 17, tzinfo=dt.UTC)
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID: Final = DataSourceId(provider="alpaca", feed="sip")
_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=30,
    derived_retention_days=365,
    deletion_required=True,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)


class UsRuntimeCapabilityProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US runtime capability projection is invalid"


@dataclass(frozen=True, slots=True)
class RuntimeOwnerCapabilityAssessment:
    instrument_id: str
    symbol: str
    owner_status: str
    runtime_status: str | None
    ready: bool


@dataclass(frozen=True, slots=True)
class UsRuntimeCapabilityProjection:
    cycle_id: str
    assessed_at: dt.datetime
    complete: bool
    owners: tuple[RuntimeOwnerCapabilityAssessment, ...]
    capability: DataCapability
    entitlement: DataEntitlement


def project_us_runtime_capability(
    audit: RuntimeFleetAuditRecord,
) -> UsRuntimeCapabilityProjection:
    try:
        validate_runtime_fleet_audit(audit)
        owners = tuple(_owner(item) for item in audit.owners)
        _validate_aggregate(audit, owners)
        ready_count = sum(item.ready for item in owners)
        completeness_bps = ready_count * 10_000 // len(owners)
        health = _health(ready_count, len(owners))
        return UsRuntimeCapabilityProjection(
            cycle_id=audit.cycle_id,
            assessed_at=audit.evaluated_at,
            complete=health is DataHealthState.COMPLETE,
            owners=owners,
            capability=_capability(audit.evaluated_at, health, completeness_bps),
            entitlement=_entitlement(),
        )
    except (AttributeError, TypeError, ValueError):
        raise UsRuntimeCapabilityProjectionError from None


def _owner(owner: RuntimeOwnerAudit) -> RuntimeOwnerCapabilityAssessment:
    owner_status = RuntimeOwnerStatus(owner.owner_status)
    runtime_status = None if owner.runtime_status is None else MarketDataRuntimeStatus(owner.runtime_status)
    if _SHA256.fullmatch(owner.profile_evidence_sha256) is None:
        raise ValueError
    ready = owner_status is RuntimeOwnerStatus.READY
    if ready:
        valid = (
            runtime_status is MarketDataRuntimeStatus.READY
            and owner.connection_epoch is not None
            and owner.last_sequence is not None
            and owner.last_sequence > 0
            and owner.feature_identity_sha256 is not None
            and _SHA256.fullmatch(owner.feature_identity_sha256) is not None
        )
    elif owner_status is RuntimeOwnerStatus.BLOCKED:
        valid = (
            runtime_status is not None
            and runtime_status is not MarketDataRuntimeStatus.READY
            and owner.feature_identity_sha256 is None
        )
    else:
        valid = (
            runtime_status is None
            and owner.connection_epoch is None
            and owner.last_sequence is None
            and owner.feature_identity_sha256 is None
        )
    if not valid:
        raise ValueError
    return RuntimeOwnerCapabilityAssessment(
        instrument_id=owner.instrument_id,
        symbol=owner.symbol,
        owner_status=owner.owner_status,
        runtime_status=owner.runtime_status,
        ready=ready,
    )


def _validate_aggregate(
    audit: RuntimeFleetAuditRecord,
    owners: tuple[RuntimeOwnerCapabilityAssessment, ...],
) -> None:
    policy = SubscriptionPolicyStatus(audit.policy_status)
    fleet = RuntimeFleetStatus(audit.fleet_status)
    symbols = tuple(item.symbol for item in owners)
    expected_fleet = RuntimeFleetStatus.READY if all(item.ready for item in owners) else RuntimeFleetStatus.DEGRADED
    gate_valid = (audit.gate_status == "ready" and audit.gate_reason is None and audit.opportunity_id is not None) or (
        audit.gate_status == "blocked" and audit.gate_reason is not None and audit.opportunity_id is None
    )
    if (
        policy is not SubscriptionPolicyStatus.READY
        or fleet is not expected_fleet
        or audit.evaluated_at < _EFFECTIVE_FROM
        or len(symbols) != len(set(symbols))
        or not gate_valid
    ):
        raise ValueError


def _health(ready_count: int, total_count: int) -> DataHealthState:
    if ready_count == total_count:
        return DataHealthState.COMPLETE
    if ready_count:
        return DataHealthState.DEGRADED
    return DataHealthState.FAILED


def _capability(
    assessed_at: dt.datetime,
    health: DataHealthState,
    completeness_bps: int,
) -> DataCapability:
    return DataCapability(
        source_id=_SOURCE_ID,
        source_class=DataSourceClass.MARKET_MICROSTRUCTURE,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=("minute_bar",),
        universe="us_equities:bounded_runtime",
        delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
        expected_latency_ms=5_000,
        timestamp_semantics=(TimestampSemantic.EVENT_TIME, TimestampSemantic.RECEIVED_AT),
        retention=_RETENTION,
        rate_limits=DataRateLimits(requests_per_minute=60),
        freshness_slo_seconds=60,
        completeness_slo_bps=10_000,
        health_state=health,
        assessed_at=assessed_at,
        latest_event_received_at=None,
        latest_source_heartbeat_at=assessed_at,
        observed_completeness_bps=completeness_bps,
    )


def _entitlement() -> DataEntitlement:
    return DataEntitlement(
        entitlement_id="alpaca-sip-minute-bars-paper-recommendation-v1",
        source_id=_SOURCE_ID,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=("minute_bar",),
        permitted_uses=(DataUse.PAPER_RECOMMENDATION,),
        real_time=True,
        historical=False,
        redistribution_policy=RedistributionPolicy.DERIVED_ONLY,
        retention=_RETENTION,
        effective_from=_EFFECTIVE_FROM,
    )


__all__ = (
    "RuntimeOwnerCapabilityAssessment",
    "UsRuntimeCapabilityProjection",
    "UsRuntimeCapabilityProjectionError",
    "project_us_runtime_capability",
)
