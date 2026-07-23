from __future__ import annotations

from dataclasses import dataclass
from typing import override

from pydantic import ValidationError

from trading_agent.data_capability_models import (
    DataCapability,
    DataDeliveryMode,
    DataEntitlement,
    DataHealthState,
    DataRateLimits,
    DataSourceClass,
    TimestampSemantic,
)
from trading_agent.issuer_announcement_models import (
    IssuerAnnouncementRequest,
    IssuerAnnouncementRunStatus,
    IssuerAnnouncementTerminal,
)
from trading_agent.security_master_models import DataMarketDomain


class IssuerAnnouncementCapabilityError(ValueError):
    @override
    def __str__(self) -> str:
        return "issuer announcement capability projection is invalid"


@dataclass(frozen=True, slots=True)
class IssuerAnnouncementCapabilityProjection:
    complete: bool
    capability: DataCapability
    entitlement: DataEntitlement


def project_issuer_announcement_capability(
    request: IssuerAnnouncementRequest,
    terminal: IssuerAnnouncementTerminal,
) -> IssuerAnnouncementCapabilityProjection:
    try:
        validated_request = IssuerAnnouncementRequest.model_validate(request.model_dump())
        validated_terminal = IssuerAnnouncementTerminal.model_validate(
            terminal.model_dump()
        )
        if (
            validated_terminal.request_id != validated_request.request_id
            or validated_terminal.completed_at < validated_request.requested_at
        ):
            raise IssuerAnnouncementCapabilityError
        onboarding = validated_request.onboarding
        complete = validated_terminal.status is IssuerAnnouncementRunStatus.SUCCESS
        capability = DataCapability(
            source_id=onboarding.source_id,
            source_class=DataSourceClass.NEWS_EVENTS,
            market_domains=(DataMarketDomain.US_EQUITIES,),
            event_types=("issuer_announcement",),
            universe="us_equities:bounded_issuer",
            delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
            historical_from=validated_request.requested_at.date() if complete else None,
            expected_latency_ms=min(30_000, onboarding.freshness_slo_seconds * 1_000),
            timestamp_semantics=(
                TimestampSemantic.PUBLISHED_AT,
                TimestampSemantic.RECEIVED_AT,
            ),
            retention=onboarding.retention,
            rate_limits=DataRateLimits(
                requests_per_minute=onboarding.max_requests_per_minute
            ),
            freshness_slo_seconds=onboarding.freshness_slo_seconds,
            completeness_slo_bps=10_000,
            health_state=(
                DataHealthState.COMPLETE if complete else DataHealthState.FAILED
            ),
            assessed_at=validated_terminal.completed_at,
            latest_event_received_at=(
                validated_terminal.completed_at
                if validated_terminal.announcement_count > 0
                else None
            ),
            latest_source_heartbeat_at=validated_terminal.completed_at,
            observed_completeness_bps=10_000 if complete else 0,
        )
        entitlement = DataEntitlement(
            entitlement_id=onboarding.onboarding_id,
            source_id=onboarding.source_id,
            market_domains=(DataMarketDomain.US_EQUITIES,),
            event_types=("issuer_announcement",),
            permitted_uses=onboarding.permitted_uses,
            real_time=False,
            historical=True,
            redistribution_policy=onboarding.redistribution_policy,
            retention=onboarding.retention,
            effective_from=onboarding.effective_from,
            effective_to=onboarding.effective_to,
        )
        return IssuerAnnouncementCapabilityProjection(
            complete=complete,
            capability=capability,
            entitlement=entitlement,
        )
    except IssuerAnnouncementCapabilityError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise IssuerAnnouncementCapabilityError from None


__all__ = (
    "IssuerAnnouncementCapabilityError",
    "IssuerAnnouncementCapabilityProjection",
    "project_issuer_announcement_capability",
)
