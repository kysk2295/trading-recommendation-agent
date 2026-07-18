from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.canonical_event_models import CanonicalEntityRef
from trading_agent.data_capability_models import DataSourceId

_OPAQUE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class ResearchEvidenceContractError(ValueError):
    pass


class ExtractionMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"


class ClaimStance(StrEnum):
    SUPPORTS = "supports"
    DISPUTES = "disputes"
    REPORTS = "reports"
    SPECULATIVE = "speculative"


class ClaimCorroborationStatus(StrEnum):
    UNCONFIRMED = "unconfirmed"
    CORROBORATED = "corroborated"
    CONFLICTED = "conflicted"


class ClaimNoveltyStatus(StrEnum):
    NOVEL = "novel"
    BURST = "burst"
    RECURRING = "recurring"


class ResearchClaimExtraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str
    event_content_hash: str
    source_id: DataSourceId
    raw_receipt_ref: str
    entity_refs: tuple[CanonicalEntityRef, ...]
    claim_key: str
    claim_kind: str
    stance: ClaimStance
    confidence_bps: int
    extracted_at: dt.datetime
    extraction_method: ExtractionMethod
    extractor_version: str
    model_version: str | None
    prompt_version: str | None
    output_sha256: str

    @model_validator(mode="after")
    def validate_extraction(self) -> Self:
        entity_ids = tuple(item.canonical_id for item in self.entity_refs)
        llm_shape = (
            self.model_version is not None
            and _OPAQUE_ID.fullmatch(self.model_version) is not None
            and self.prompt_version is not None
            and _OPAQUE_ID.fullmatch(self.prompt_version) is not None
        )
        deterministic_shape = self.model_version is None and self.prompt_version is None
        if (
            _OPAQUE_ID.fullmatch(self.event_id) is None
            or _SHA256.fullmatch(self.event_content_hash) is None
            or _OPAQUE_ID.fullmatch(self.raw_receipt_ref) is None
            or not self.entity_refs
            or entity_ids != tuple(sorted(set(entity_ids)))
            or _OPAQUE_ID.fullmatch(self.claim_key) is None
            or _OPAQUE_ID.fullmatch(self.claim_kind) is None
            or not 0 <= self.confidence_bps <= 10_000
            or not _aware(self.extracted_at)
            or _OPAQUE_ID.fullmatch(self.extractor_version) is None
            or (self.extraction_method is ExtractionMethod.LLM and not llm_shape)
            or (self.extraction_method is ExtractionMethod.DETERMINISTIC and not deterministic_shape)
            or _SHA256.fullmatch(self.output_sha256) is None
        ):
            raise ResearchEvidenceContractError("invalid research claim extraction")
        return self

    @property
    def evidence_id(self) -> str:
        return _sha256(self.model_dump(mode="json"))


class ResearchClaimSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    claim_snapshot_id: str
    claim_key: str
    claim_kind: str
    entity_refs: tuple[CanonicalEntityRef, ...]
    evidence_ids: tuple[str, ...]
    baseline_evidence_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    first_observed_at: dt.datetime
    latest_observed_at: dt.datetime
    current_evidence_count: int
    baseline_evidence_count: int
    independent_source_count: int
    supporting_evidence_count: int
    disputing_evidence_count: int
    reporting_evidence_count: int
    speculative_evidence_count: int
    minimum_confidence_bps: int
    corroboration_status: ClaimCorroborationStatus
    novelty_status: ClaimNoveltyStatus

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        entity_ids = tuple(item.canonical_id for item in self.entity_refs)
        stance_total = (
            self.supporting_evidence_count
            + self.disputing_evidence_count
            + self.reporting_evidence_count
            + self.speculative_evidence_count
        )
        if (
            _SHA256.fullmatch(self.claim_snapshot_id) is None
            or _OPAQUE_ID.fullmatch(self.claim_key) is None
            or _OPAQUE_ID.fullmatch(self.claim_kind) is None
            or not self.entity_refs
            or entity_ids != tuple(sorted(set(entity_ids)))
            or not self.evidence_ids
            or self.evidence_ids != tuple(sorted(set(self.evidence_ids)))
            or any(_SHA256.fullmatch(item) is None for item in self.evidence_ids)
            or self.baseline_evidence_ids != tuple(sorted(set(self.baseline_evidence_ids)))
            or any(_SHA256.fullmatch(item) is None for item in self.baseline_evidence_ids)
            or not self.source_ids
            or self.source_ids != tuple(sorted(set(self.source_ids)))
            or not _aware(self.first_observed_at)
            or not _aware(self.latest_observed_at)
            or self.latest_observed_at < self.first_observed_at
            or self.current_evidence_count != len(self.evidence_ids)
            or self.current_evidence_count != stance_total
            or self.baseline_evidence_count != len(self.baseline_evidence_ids)
            or self.independent_source_count != len(self.source_ids)
            or not 0 <= self.minimum_confidence_bps <= 10_000
        ):
            raise ResearchEvidenceContractError("invalid research claim snapshot")
        payload = self.model_dump(mode="json")
        _ = payload.pop("claim_snapshot_id")
        if self.claim_snapshot_id != _sha256(payload):
            raise ResearchEvidenceContractError("invalid research claim snapshot identity")
        return self


class ResearchEvidenceReadModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    as_of: dt.datetime
    current_window_seconds: int
    baseline_window_seconds: int
    burst_threshold_bps: int
    source_event_count: int
    extraction_count: int
    claims: tuple[ResearchClaimSnapshot, ...]
    content_sha256: str

    @model_validator(mode="after")
    def validate_read_model(self) -> Self:
        claim_ids = tuple(item.claim_snapshot_id for item in self.claims)
        if (
            not _aware(self.as_of)
            or not 1 <= self.current_window_seconds <= 86_400
            or not self.current_window_seconds <= self.baseline_window_seconds <= 2_592_000
            or not 10_000 <= self.burst_threshold_bps <= 100_000
            or self.source_event_count <= 0
            or self.extraction_count <= 0
            or not self.claims
            or claim_ids != tuple(sorted(set(claim_ids)))
            or _SHA256.fullmatch(self.content_sha256) is None
        ):
            raise ResearchEvidenceContractError("invalid research evidence read model")
        payload = self.model_dump(mode="json")
        _ = payload.pop("content_sha256")
        if self.content_sha256 != _sha256(payload):
            raise ResearchEvidenceContractError("invalid research evidence identity")
        return self


def content_sha256(value: JsonValue) -> str:
    return _sha256(value)


def _sha256(value: JsonValue) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "ClaimCorroborationStatus",
    "ClaimNoveltyStatus",
    "ClaimStance",
    "ExtractionMethod",
    "ResearchClaimExtraction",
    "ResearchClaimSnapshot",
    "ResearchEvidenceReadModel",
    "content_sha256",
)
