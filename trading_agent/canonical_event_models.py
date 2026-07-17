from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.data_capability_models import DataSourceId

_OPAQUE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_EVENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_QUALITY_FLAG = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CanonicalEntityType(StrEnum):
    INSTRUMENT = "instrument"
    ORGANIZATION = "organization"
    PERSON = "person"
    MACRO_SERIES = "macro_series"
    TOPIC = "topic"


class CanonicalEventOperation(StrEnum):
    ORIGINAL = "original"
    CORRECTION = "correction"
    TOMBSTONE = "tombstone"


class CanonicalEntityRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    entity_type: CanonicalEntityType
    entity_id: str

    @model_validator(mode="after")
    def validate_entity(self) -> Self:
        if _OPAQUE_ID.fullmatch(self.entity_id) is None:
            raise ValueError("invalid canonical entity reference")
        return self

    @property
    def canonical_id(self) -> str:
        return f"{self.entity_type.value}:{self.entity_id}"


class CanonicalEventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str
    source_id: DataSourceId
    provider_event_id: str | None = None
    entity_refs: tuple[CanonicalEntityRef, ...]
    event_type: str
    event_time: dt.datetime | None = None
    published_at: dt.datetime | None = None
    provider_time: dt.datetime | None = None
    received_at: dt.datetime
    normalized_at: dt.datetime
    effective_from: dt.datetime | None = None
    effective_to: dt.datetime | None = None
    sequence_or_offset: str | None = None
    operation: CanonicalEventOperation
    correction_of: str | None = None
    raw_receipt_ref: str
    content_hash: str
    quality_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        entity_ids = tuple(entity.canonical_id for entity in self.entity_refs)
        optional_timestamps = (
            self.event_time,
            self.published_at,
            self.provider_time,
            self.effective_from,
            self.effective_to,
        )
        optional_times_valid = all(value is None or _aware(value) for value in optional_timestamps)
        received_aware = _aware(self.received_at)
        normalized_aware = _aware(self.normalized_at)
        processing_order_valid = (
            received_aware
            and normalized_aware
            and self.normalized_at >= self.received_at
        )
        effective_valid = (
            self.effective_to is None
            or (
                self.effective_from is not None
                and _aware(self.effective_from)
                and _aware(self.effective_to)
                and self.effective_to > self.effective_from
            )
        )
        correction_shape_valid = (
            self.correction_of is None
            if self.operation is CanonicalEventOperation.ORIGINAL
            else self.correction_of is not None
            and _OPAQUE_ID.fullmatch(self.correction_of) is not None
            and self.correction_of != self.event_id
        )
        if (
            _OPAQUE_ID.fullmatch(self.event_id) is None
            or (
                self.provider_event_id is not None
                and not _canonical_text(self.provider_event_id, max_length=512)
            )
            or not self.entity_refs
            or entity_ids != tuple(sorted(set(entity_ids)))
            or _EVENT_TYPE.fullmatch(self.event_type) is None
            or not optional_times_valid
            or not processing_order_valid
            or not effective_valid
            or (
                self.sequence_or_offset is not None
                and not _canonical_text(self.sequence_or_offset, max_length=256)
            )
            or not correction_shape_valid
            or _OPAQUE_ID.fullmatch(self.raw_receipt_ref) is None
            or _SHA256.fullmatch(self.content_hash) is None
            or not _canonical_quality_flags(self.quality_flags)
        ):
            raise ValueError("invalid canonical event envelope")
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str, *, max_length: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(character in value for character in "\r\n\t")
    )


def _canonical_quality_flags(flags: tuple[str, ...]) -> bool:
    return flags == tuple(sorted(set(flags))) and all(_QUALITY_FLAG.fullmatch(flag) for flag in flags)


__all__ = (
    "CanonicalEntityRef",
    "CanonicalEntityType",
    "CanonicalEventEnvelope",
    "CanonicalEventOperation",
)
