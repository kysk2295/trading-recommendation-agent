from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

from trading_agent.arxiv_research_models import ArxivRunStatus, ArxivTerminal
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

_SOURCE: Final = DataSourceId(provider="arxiv", feed="api_query")
_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=365,
    derived_retention_days=3_650,
    deletion_required=False,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)


class ArxivCapabilityError(ValueError):
    @override
    def __str__(self) -> str:
        return "arXiv research capability is invalid"


@dataclass(frozen=True, slots=True)
class ArxivCapabilityProjection:
    capability: DataCapability
    entitlement: DataEntitlement


def project_arxiv_capability(terminal: ArxivTerminal) -> ArxivCapabilityProjection:
    try:
        checked = ArxivTerminal.model_validate(terminal.model_dump(mode="python"))
        complete = checked.status is ArxivRunStatus.SUCCESS
        snapshot = checked.snapshot
        capability = DataCapability(
            source_id=_SOURCE,
            source_class=DataSourceClass.RESEARCH_KNOWLEDGE,
            market_domains=(DataMarketDomain.RESEARCH_KNOWLEDGE,),
            event_types=("academic_paper_metadata",),
            universe="research_knowledge:bounded_arxiv_query",
            delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
            historical_from=(
                min(paper.published_at.date() for paper in snapshot.papers)
                if snapshot is not None
                else None
            ),
            expected_latency_ms=86_400_000,
            timestamp_semantics=(
                TimestampSemantic.PUBLISHED_AT,
                TimestampSemantic.RECEIVED_AT,
            ),
            retention=_RETENTION,
            rate_limits=DataRateLimits(requests_per_minute=1),
            freshness_slo_seconds=86_400,
            completeness_slo_bps=10_000,
            health_state=DataHealthState.COMPLETE if complete else DataHealthState.FAILED,
            assessed_at=checked.completed_at,
            latest_event_received_at=(
                snapshot.observed_at if snapshot is not None else None
            ),
            latest_source_heartbeat_at=checked.completed_at,
            observed_completeness_bps=10_000 if complete else 0,
        )
        entitlement = DataEntitlement(
            entitlement_id="arxiv-api-research-metadata-v1",
            source_id=_SOURCE,
            market_domains=(DataMarketDomain.RESEARCH_KNOWLEDGE,),
            event_types=("academic_paper_metadata",),
            permitted_uses=(
                DataUse.HISTORICAL_RESEARCH,
                DataUse.SHADOW_FORWARD,
            ),
            real_time=False,
            historical=True,
            redistribution_policy=RedistributionPolicy.ATTRIBUTED_SUMMARY,
            retention=_RETENTION,
            effective_from=dt.datetime(2026, 7, 24, tzinfo=dt.UTC),
        )
        return ArxivCapabilityProjection(capability, entitlement)
    except (TypeError, ValidationError, ValueError):
        raise ArxivCapabilityError from None


__all__ = (
    "ArxivCapabilityError",
    "ArxivCapabilityProjection",
    "project_arxiv_capability",
)
