from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_OPAQUE_ID = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class InvalidRawObjectManifestError(ValueError):
    @override
    def __str__(self) -> str:
        return "raw object manifest is invalid"


@dataclass(frozen=True, slots=True)
class RawReceiptPayload:
    value: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, bytes):
            raise ValueError("invalid raw receipt payload")


class RawReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    schema_version: Literal[1] = 1
    receipt_id: str
    source_id: str
    market_date: dt.date
    received_at: dt.datetime
    payload_sha256: str
    payload: RawReceiptPayload = Field(repr=False)

    @field_validator("received_at")
    @classmethod
    def normalize_received_at(cls, value: dt.datetime) -> dt.datetime:
        if not _aware(value):
            raise ValueError("invalid raw receipt")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if (
            _OPAQUE_ID.fullmatch(self.receipt_id) is None
            or _SOURCE_ID.fullmatch(self.source_id) is None
            or isinstance(self.market_date, dt.datetime)
            or _SHA256.fullmatch(self.payload_sha256) is None
            or not isinstance(self.payload, RawReceiptPayload)
            or hashlib.sha256(self.payload.value).hexdigest() != self.payload_sha256
        ):
            raise ValueError("invalid raw receipt")
        return self


class RawObjectReceiptReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str
    received_at: dt.datetime
    payload_sha256: str
    byte_size: int

    @field_validator("received_at")
    @classmethod
    def normalize_received_at(cls, value: dt.datetime) -> dt.datetime:
        if not _aware(value):
            raise ValueError("invalid raw object receipt reference")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if (
            _OPAQUE_ID.fullmatch(self.receipt_id) is None
            or _SHA256.fullmatch(self.payload_sha256) is None
            or self.byte_size < 0
        ):
            raise ValueError("invalid raw object receipt reference")
        return self


class RawObjectPartitionManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest_id: str
    content_sha256: str
    source_id: str
    market_date: dt.date
    received_at_start: dt.datetime
    received_at_end: dt.datetime
    receipt_count: int
    total_byte_size: int
    parent_ledger_generation: int
    receipts: tuple[RawObjectReceiptReference, ...]

    @field_validator("received_at_start", "received_at_end")
    @classmethod
    def normalize_received_range(cls, value: dt.datetime) -> dt.datetime:
        if not _aware(value):
            raise ValueError("invalid raw object partition manifest")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        receipt_ids = tuple(item.receipt_id for item in self.receipts)
        if (
            _OPAQUE_ID.fullmatch(self.manifest_id) is None
            or _SHA256.fullmatch(self.content_sha256) is None
            or _SOURCE_ID.fullmatch(self.source_id) is None
            or isinstance(self.market_date, dt.datetime)
            or self.received_at_start > self.received_at_end
            or self.receipt_count < 0
            or self.total_byte_size < 0
            or self.parent_ledger_generation < 0
            or not self.receipts
            or self.receipt_count != len(self.receipts)
            or receipt_ids != tuple(sorted(set(receipt_ids)))
            or self.total_byte_size != sum(item.byte_size for item in self.receipts)
            or self.received_at_start != min(item.received_at for item in self.receipts)
            or self.received_at_end != max(item.received_at for item in self.receipts)
            or self.manifest_id != _manifest_content_sha256(self)
            or self.content_sha256 != self.manifest_id
        ):
            raise ValueError("invalid raw object partition manifest")
        return self


class RawReceiptProjectionFixtureReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str
    received_at: dt.datetime
    payload_sha256: str
    payload_base64: str = Field(repr=False)

    @field_validator("received_at")
    @classmethod
    def normalize_received_at(cls, value: dt.datetime) -> dt.datetime:
        if not _aware(value):
            raise ValueError("invalid raw receipt projection fixture")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_fixture_receipt(self) -> Self:
        if (
            _OPAQUE_ID.fullmatch(self.receipt_id) is None
            or _SHA256.fullmatch(self.payload_sha256) is None
            or not self.payload_base64
        ):
            raise ValueError("invalid raw receipt projection fixture")
        return self


class RawReceiptProjectionFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_id: str
    market_date: dt.date
    parent_ledger_generation: int
    receipts: tuple[RawReceiptProjectionFixtureReceipt, ...]

    @model_validator(mode="after")
    def validate_fixture(self) -> Self:
        if (
            _SOURCE_ID.fullmatch(self.source_id) is None
            or isinstance(self.market_date, dt.datetime)
            or self.parent_ledger_generation < 0
            or not self.receipts
        ):
            raise ValueError("invalid raw receipt projection fixture")
        return self


def _manifest_content_sha256(manifest: RawObjectPartitionManifest) -> str:
    content = {
        "schema_version": manifest.schema_version,
        "source_id": manifest.source_id,
        "market_date": manifest.market_date.isoformat(),
        "received_at_start": _canonical_time(manifest.received_at_start),
        "received_at_end": _canonical_time(manifest.received_at_end),
        "receipt_count": manifest.receipt_count,
        "total_byte_size": manifest.total_byte_size,
        "parent_ledger_generation": manifest.parent_ledger_generation,
        "receipts": [
            {
                "receipt_id": receipt.receipt_id,
                "received_at": _canonical_time(receipt.received_at),
                "payload_sha256": receipt.payload_sha256,
                "byte_size": receipt.byte_size,
            }
            for receipt in manifest.receipts
        ],
    }
    canonical = json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_time(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
