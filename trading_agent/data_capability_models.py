from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.research_identity_models import StrategyLaneRef
from trading_agent.security_master_models import DataMarketDomain

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_OPAQUE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_EVENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")


class DataCapabilityContractError(ValueError):
    pass


class DataSourceClass(StrEnum):
    MARKET_MICROSTRUCTURE = "market_microstructure"
    DERIVATIVES = "derivatives"
    REGULATORY_FUNDAMENTAL = "regulatory_fundamental"
    NEWS_EVENTS = "news_events"
    SOCIAL_ATTENTION = "social_attention"
    MACRO_FLOW = "macro_flow"
    RESEARCH_KNOWLEDGE = "research_knowledge"


class DataDeliveryMode(StrEnum):
    REST_SNAPSHOT = "rest_snapshot"
    WEBSOCKET_STREAM = "websocket_stream"
    FILE_BATCH = "file_batch"
    LOCAL_DERIVED = "local_derived"


class TimestampSemantic(StrEnum):
    EVENT_TIME = "event_time"
    PUBLISHED_AT = "published_at"
    PROVIDER_TIME = "provider_time"
    RECEIVED_AT = "received_at"


class DataUse(StrEnum):
    HISTORICAL_RESEARCH = "historical_research"
    PAPER_RECOMMENDATION = "paper_recommendation"
    SHADOW_FORWARD = "shadow_forward"


class RedistributionPolicy(StrEnum):
    NONE = "none"
    DERIVED_ONLY = "derived_only"
    ATTRIBUTED_SUMMARY = "attributed_summary"


class DataCorrectionPolicy(StrEnum):
    APPEND_CORRECTION = "append_correction"
    APPEND_TOMBSTONE = "append_tombstone"


class DataHealthState(StrEnum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class DataRequirementFailureMode(StrEnum):
    BLOCKED_BY_DATA = "blocked_by_data"
    RESEARCH_ONLY = "research_only"


class DataSourceId(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    provider: str
    feed: str

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if _SLUG.fullmatch(self.provider) is None or _SLUG.fullmatch(self.feed) is None:
            raise DataCapabilityContractError("invalid data source identity")
        return self

    @property
    def canonical_id(self) -> str:
        return f"{self.provider}/{self.feed}"


class DataRetentionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_retention_days: int
    derived_retention_days: int
    deletion_required: bool
    correction_policy: DataCorrectionPolicy

    @model_validator(mode="after")
    def validate_retention(self) -> Self:
        if (
            self.raw_retention_days < 0
            or self.derived_retention_days < 0
            or self.derived_retention_days < self.raw_retention_days
        ):
            raise DataCapabilityContractError("invalid data retention policy")
        return self


class DataRateLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requests_per_minute: int | None = None
    max_connections: int | None = None
    max_subscriptions: int | None = None

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        values = (self.requests_per_minute, self.max_connections, self.max_subscriptions)
        if all(value is None for value in values) or any(value is not None and value <= 0 for value in values):
            raise DataCapabilityContractError("invalid data rate limits")
        return self


class DataEntitlement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    entitlement_id: str
    source_id: DataSourceId
    market_domains: tuple[DataMarketDomain, ...]
    event_types: tuple[str, ...]
    permitted_uses: tuple[DataUse, ...]
    real_time: bool
    historical: bool
    redistribution_policy: RedistributionPolicy
    retention: DataRetentionPolicy
    effective_from: dt.datetime
    effective_to: dt.datetime | None = None

    @model_validator(mode="after")
    def validate_entitlement(self) -> Self:
        historical_use_valid = DataUse.HISTORICAL_RESEARCH not in self.permitted_uses or self.historical
        paper_use_valid = DataUse.PAPER_RECOMMENDATION not in self.permitted_uses or self.real_time
        if (
            _OPAQUE_ID.fullmatch(self.entitlement_id) is None
            or not _canonical_enum_tuple(self.market_domains)
            or not _canonical_string_tuple(self.event_types, _EVENT_TYPE)
            or not _canonical_enum_tuple(self.permitted_uses)
            or not (self.real_time or self.historical)
            or not historical_use_valid
            or not paper_use_valid
            or not _aware(self.effective_from)
            or (
                self.effective_to is not None
                and (not _aware(self.effective_to) or self.effective_to <= self.effective_from)
            )
        ):
            raise DataCapabilityContractError("invalid data entitlement")
        return self


class DataCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_id: DataSourceId
    source_class: DataSourceClass
    market_domains: tuple[DataMarketDomain, ...]
    event_types: tuple[str, ...]
    universe: str
    delivery_modes: tuple[DataDeliveryMode, ...]
    historical_from: dt.date | None = None
    expected_latency_ms: int
    timestamp_semantics: tuple[TimestampSemantic, ...]
    retention: DataRetentionPolicy
    rate_limits: DataRateLimits
    freshness_slo_seconds: int
    completeness_slo_bps: int
    health_state: DataHealthState
    assessed_at: dt.datetime
    latest_event_received_at: dt.datetime | None = None
    latest_source_heartbeat_at: dt.datetime | None = None
    observed_completeness_bps: int

    @model_validator(mode="after")
    def validate_capability(self) -> Self:
        current_health = self.health_state in {DataHealthState.COMPLETE, DataHealthState.DEGRADED}
        assessed_aware = _aware(self.assessed_at)
        latest_valid = (
            self.latest_event_received_at is not None
            and _aware(self.latest_event_received_at)
            and assessed_aware
            and self.latest_event_received_at <= self.assessed_at
        )
        heartbeat_valid = (
            self.latest_source_heartbeat_at is not None
            and _aware(self.latest_source_heartbeat_at)
            and assessed_aware
            and self.latest_source_heartbeat_at <= self.assessed_at
        )
        complete_valid = (
            self.health_state is not DataHealthState.COMPLETE
            or self.observed_completeness_bps >= self.completeness_slo_bps
        )
        if (
            not _canonical_enum_tuple(self.market_domains)
            or not _canonical_string_tuple(self.event_types, _EVENT_TYPE)
            or _OPAQUE_ID.fullmatch(self.universe) is None
            or not _canonical_enum_tuple(self.delivery_modes)
            or self.expected_latency_ms < 0
            or not _canonical_enum_tuple(self.timestamp_semantics)
            or not 1 <= self.freshness_slo_seconds <= 86_400
            or self.expected_latency_ms > self.freshness_slo_seconds * 1_000
            or not 1 <= self.completeness_slo_bps <= 10_000
            or not 0 <= self.observed_completeness_bps <= 10_000
            or not assessed_aware
            or (self.latest_event_received_at is not None and not latest_valid)
            or (self.latest_source_heartbeat_at is not None and not heartbeat_valid)
            or (current_health and not (latest_valid or heartbeat_valid))
            or not complete_valid
        ):
            raise DataCapabilityContractError("invalid data capability")
        return self


class StrategyDataRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    requirement_id: str
    strategy_lane: StrategyLaneRef
    data_use: DataUse
    market_domain: DataMarketDomain
    event_type: str
    primary_source_id: DataSourceId
    fallback_source_ids: tuple[DataSourceId, ...] = ()
    required_delivery_modes: tuple[DataDeliveryMode, ...]
    required_timestamp_semantics: tuple[TimestampSemantic, ...]
    max_age_seconds: int
    minimum_completeness_bps: int
    minimum_historical_start: dt.date | None = None
    allow_degraded: bool
    failure_mode: DataRequirementFailureMode

    @model_validator(mode="after")
    def validate_requirement(self) -> Self:
        fallback_ids = tuple(source.canonical_id for source in self.fallback_source_ids)
        if (
            _OPAQUE_ID.fullmatch(self.requirement_id) is None
            or _EVENT_TYPE.fullmatch(self.event_type) is None
            or len(fallback_ids) != len(set(fallback_ids))
            or self.primary_source_id.canonical_id in fallback_ids
            or not _canonical_enum_tuple(self.required_delivery_modes)
            or not _canonical_enum_tuple(self.required_timestamp_semantics)
            or not 1 <= self.max_age_seconds <= 86_400
            or not 1 <= self.minimum_completeness_bps <= 10_000
            or (self.data_use is DataUse.HISTORICAL_RESEARCH and self.minimum_historical_start is None)
        ):
            raise DataCapabilityContractError("invalid strategy data requirement")
        return self

    @property
    def declared_source_ids(self) -> tuple[DataSourceId, ...]:
        return (self.primary_source_id, *self.fallback_source_ids)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_enum_tuple(values: tuple[StrEnum, ...]) -> bool:
    serialized = tuple(value.value for value in values)
    return bool(values) and serialized == tuple(sorted(set(serialized)))


def _canonical_string_tuple(values: tuple[str, ...], pattern: re.Pattern[str]) -> bool:
    return bool(values) and values == tuple(sorted(set(values))) and all(pattern.fullmatch(value) for value in values)


__all__ = (
    "DataCapability",
    "DataCorrectionPolicy",
    "DataDeliveryMode",
    "DataEntitlement",
    "DataHealthState",
    "DataRateLimits",
    "DataRequirementFailureMode",
    "DataRetentionPolicy",
    "DataSourceClass",
    "DataSourceId",
    "DataUse",
    "RedistributionPolicy",
    "StrategyDataRequirement",
    "TimestampSemantic",
)
