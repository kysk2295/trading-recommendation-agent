from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.canonical_event_models import CanonicalEntityRef
from trading_agent.data_capability_models import (
    DataCorrectionPolicy,
    DataRetentionPolicy,
    DataSourceId,
    RedistributionPolicy,
)

_OPAQUE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLATFORM = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class SocialEvidenceContractError(ValueError):
    pass


class SocialPlatform(StrEnum):
    X = "x"
    REDDIT = "reddit"


class SocialOperatingMode(StrEnum):
    """Social evidence may only feed shadow research, never paper orders."""

    SHADOW_RESEARCH_ONLY = "shadow_research_only"


class SocialEntitlementContract(BaseModel):
    """Official API entitlement + retention before any social connector opens."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_id: DataSourceId
    platform: SocialPlatform
    entitlement_id: str
    effective_from: dt.datetime
    effective_to: dt.datetime | None = None
    operating_mode: SocialOperatingMode = SocialOperatingMode.SHADOW_RESEARCH_ONLY
    redistribution: RedistributionPolicy
    retention: DataRetentionPolicy
    allows_raw_text_storage: bool
    official_api_only: Literal[True] = True
    unauthorized_crawl_forbidden: Literal[True] = True

    @model_validator(mode="after")
    def validate_entitlement(self) -> Self:
        expected_feed = "x" if self.platform is SocialPlatform.X else "reddit"
        correction_ok = self.retention.correction_policy in (
            DataCorrectionPolicy.APPEND_CORRECTION,
            DataCorrectionPolicy.APPEND_TOMBSTONE,
        )
        raw_policy_ok = (not self.allows_raw_text_storage) or (
            self.redistribution is RedistributionPolicy.NONE
        )
        if (
            self.source_id.provider != self.platform.value
            or self.source_id.feed != expected_feed
            or _OPAQUE_ID.fullmatch(self.entitlement_id) is None
            or not _aware(self.effective_from)
            or (self.effective_to is not None and not _aware(self.effective_to))
            or (self.effective_to is not None and self.effective_to <= self.effective_from)
            or self.operating_mode is not SocialOperatingMode.SHADOW_RESEARCH_ONLY
            or not correction_ok
            or not raw_policy_ok
        ):
            raise SocialEvidenceContractError("invalid social entitlement contract")
        return self


class SocialPostObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    platform: SocialPlatform
    provider_post_id: str
    author_id: str
    community_id: str | None = None
    language: str
    posted_at: dt.datetime
    received_at: dt.datetime
    deleted_or_withheld: bool
    spam_or_bot_score_bps: int = Field(ge=0, le=10_000)
    raw_text_stored: bool
    content_fingerprint: str

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if (
            _OPAQUE_ID.fullmatch(self.provider_post_id) is None
            or _OPAQUE_ID.fullmatch(self.author_id) is None
            or (self.community_id is not None and _OPAQUE_ID.fullmatch(self.community_id) is None)
            or _PLATFORM.fullmatch(self.language) is None
            or not _aware(self.posted_at)
            or not _aware(self.received_at)
            or self.received_at < self.posted_at
            or _SHA256.fullmatch(self.content_fingerprint) is None
        ):
            raise SocialEvidenceContractError("invalid social post observation")
        return self


class SocialEvidenceSnapshot(BaseModel):
    """Point-in-time social attention evidence. Not a recommendation or order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_id: str
    source_id: DataSourceId
    entitlement_id: str
    observed_at: dt.datetime
    entity_refs: tuple[CanonicalEntityRef, ...] = Field(min_length=1, max_length=32)
    observations: tuple[SocialPostObservation, ...] = Field(max_length=200)
    independent_author_count: int = Field(ge=0, le=200)
    novelty_score_bps: int = Field(ge=0, le=10_000)
    burst_score_bps: int = Field(ge=0, le=10_000)
    operating_mode: SocialOperatingMode = SocialOperatingMode.SHADOW_RESEARCH_ONLY
    order_authority: Literal[False] = False
    lifecycle_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        entity_ids = tuple(item.canonical_id for item in self.entity_refs)
        author_ids = tuple(item.author_id for item in self.observations)
        if (
            _SHA256.fullmatch(self.snapshot_id) is None
            or _OPAQUE_ID.fullmatch(self.entitlement_id) is None
            or not _aware(self.observed_at)
            or entity_ids != tuple(sorted(set(entity_ids)))
            or any(item.received_at > self.observed_at for item in self.observations)
            or self.independent_author_count != len(set(author_ids))
            or self.operating_mode is not SocialOperatingMode.SHADOW_RESEARCH_ONLY
            or self.order_authority is not False
            or self.lifecycle_authority is not False
        ):
            raise SocialEvidenceContractError("invalid social evidence snapshot")
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "SocialEntitlementContract",
    "SocialEvidenceContractError",
    "SocialEvidenceSnapshot",
    "SocialOperatingMode",
    "SocialPlatform",
    "SocialPostObservation",
)
