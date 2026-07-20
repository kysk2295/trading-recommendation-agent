from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

from trading_agent.alpaca_news_models import AlpacaNewsRun, AlpacaNewsRunStatus
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

_SOURCE_ID: Final = DataSourceId(provider="alpaca", feed="news")
_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 21, tzinfo=dt.UTC)
_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=30,
    derived_retention_days=365,
    deletion_required=True,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)


class AlpacaNewsCapabilityProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca news capability projection is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaNewsCapabilityProjection:
    complete: bool
    page_count: int
    article_count: int
    capability: DataCapability
    entitlement: DataEntitlement


def project_alpaca_news_capability(run: AlpacaNewsRun) -> AlpacaNewsCapabilityProjection:
    try:
        validated = AlpacaNewsRun.model_validate(run.model_dump())
        if (
            validated.latest_event_at is not None
            and validated.latest_event_at > validated.completed_at
        ):
            raise AlpacaNewsCapabilityProjectionError
        complete = validated.status is AlpacaNewsRunStatus.SUCCESS
        if complete and validated.request.end_at > validated.completed_at:
            raise AlpacaNewsCapabilityProjectionError
        capability = DataCapability(
            source_id=_SOURCE_ID,
            source_class=DataSourceClass.NEWS_EVENTS,
            market_domains=(DataMarketDomain.US_EQUITIES,),
            event_types=("news_article",),
            universe="us_equities:bounded_symbols",
            delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
            historical_from=validated.request.start_at.date() if complete else None,
            expected_latency_ms=30_000,
            timestamp_semantics=(
                TimestampSemantic.PROVIDER_TIME,
                TimestampSemantic.PUBLISHED_AT,
                TimestampSemantic.RECEIVED_AT,
            ),
            retention=_RETENTION,
            rate_limits=DataRateLimits(requests_per_minute=60),
            freshness_slo_seconds=300,
            completeness_slo_bps=10_000,
            health_state=DataHealthState.COMPLETE if complete else DataHealthState.FAILED,
            assessed_at=validated.completed_at,
            latest_event_received_at=None,
            latest_source_heartbeat_at=validated.completed_at,
            observed_completeness_bps=10_000 if complete else 0,
        )
        entitlement = DataEntitlement(
            entitlement_id="alpaca-news-research-shadow-v1",
            source_id=_SOURCE_ID,
            market_domains=(DataMarketDomain.US_EQUITIES,),
            event_types=("news_article",),
            permitted_uses=(DataUse.HISTORICAL_RESEARCH, DataUse.SHADOW_FORWARD),
            real_time=False,
            historical=True,
            redistribution_policy=RedistributionPolicy.NONE,
            retention=_RETENTION,
            effective_from=_EFFECTIVE_FROM,
        )
        return AlpacaNewsCapabilityProjection(
            complete=complete,
            page_count=validated.page_count,
            article_count=validated.article_count,
            capability=capability,
            entitlement=entitlement,
        )
    except AlpacaNewsCapabilityProjectionError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise AlpacaNewsCapabilityProjectionError from None


__all__ = (
    "AlpacaNewsCapabilityProjection",
    "AlpacaNewsCapabilityProjectionError",
    "project_alpaca_news_capability",
)
