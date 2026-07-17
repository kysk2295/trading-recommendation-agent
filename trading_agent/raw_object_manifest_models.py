from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

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
        if type(self.value) is not bytes:
            raise ValueError("invalid raw receipt payload")


class RawReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, arbitrary_types_allowed=True)

    schema_version: Literal[1] = 1
    receipt_id: str
    source_id: str
    market_date: dt.date
    received_at: dt.datetime
    payload_sha256: str
    payload: RawReceiptPayload = Field(exclude=True, repr=False)

    @classmethod
    def from_payload(cls, **values: object) -> Self:
        return cls.model_validate(values)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_schema_version_int(cls, value: Any) -> int:
        return _exact_int(value, "invalid raw receipt")

    @field_validator("receipt_id", "source_id", "payload_sha256", mode="before")
    @classmethod
    def require_text(cls, value: Any) -> str:
        return _exact_str(value, "invalid raw receipt")

    @field_validator("market_date", mode="before")
    @classmethod
    def require_market_date(cls, value: Any, info: ValidationInfo) -> dt.date:
        return _exact_date(value, "invalid raw receipt", info)

    @field_validator("received_at", mode="before")
    @classmethod
    def normalize_received_at(cls, value: Any, info: ValidationInfo) -> dt.datetime:
        value = _exact_datetime(value, "invalid raw receipt", info)
        if not _aware(value):
            raise ValueError("invalid raw receipt")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if (
            _OPAQUE_ID.fullmatch(self.receipt_id) is None
            or _SOURCE_ID.fullmatch(self.source_id) is None
            or _SHA256.fullmatch(self.payload_sha256) is None
            or not isinstance(self.payload, RawReceiptPayload)
            or hashlib.sha256(self.payload.value).hexdigest() != self.payload_sha256
        ):
            raise ValueError("invalid raw receipt")
        return self


class RawObjectReceiptReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    receipt_id: str
    received_at: dt.datetime
    payload_sha256: str
    byte_size: int

    @field_validator("receipt_id", "payload_sha256", mode="before")
    @classmethod
    def require_text(cls, value: Any) -> str:
        return _exact_str(value, "invalid raw object receipt reference")

    @field_validator("byte_size", mode="before")
    @classmethod
    def require_byte_size(cls, value: Any) -> int:
        return _exact_int(value, "invalid raw object receipt reference")

    @field_validator("received_at", mode="before")
    @classmethod
    def normalize_received_at(cls, value: Any, info: ValidationInfo) -> dt.datetime:
        value = _exact_datetime(value, "invalid raw object receipt reference", info)
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
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

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

    @field_validator("schema_version", "receipt_count", "total_byte_size", "parent_ledger_generation", mode="before")
    @classmethod
    def require_ints(cls, value: Any) -> int:
        return _exact_int(value, "invalid raw object partition manifest")

    @field_validator("manifest_id", "content_sha256", "source_id", mode="before")
    @classmethod
    def require_text(cls, value: Any) -> str:
        return _exact_str(value, "invalid raw object partition manifest")

    @field_validator("market_date", mode="before")
    @classmethod
    def require_market_date(cls, value: Any, info: ValidationInfo) -> dt.date:
        return _exact_date(value, "invalid raw object partition manifest", info)

    @field_validator("received_at_start", "received_at_end", mode="before")
    @classmethod
    def normalize_received_range(cls, value: Any, info: ValidationInfo) -> dt.datetime:
        value = _exact_datetime(value, "invalid raw object partition manifest", info)
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
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    receipt_id: str
    received_at: dt.datetime
    payload_sha256: str
    payload_base64: str = Field(exclude=True, repr=False)

    @field_validator("receipt_id", "payload_sha256", "payload_base64", mode="before")
    @classmethod
    def require_text(cls, value: Any) -> str:
        return _exact_str(value, "invalid raw receipt projection fixture")

    @field_validator("received_at", mode="before")
    @classmethod
    def normalize_received_at(cls, value: Any) -> dt.datetime:
        if type(value) is not str:
            raise ValueError("invalid raw receipt projection fixture")
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("invalid raw receipt projection fixture") from None
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
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    source_id: str
    market_date: dt.date
    parent_ledger_generation: int
    receipts: tuple[RawReceiptProjectionFixtureReceipt, ...]

    @field_validator("schema_version", "parent_ledger_generation", mode="before")
    @classmethod
    def require_ints(cls, value: Any) -> int:
        return _exact_int(value, "invalid raw receipt projection fixture")

    @field_validator("source_id", mode="before")
    @classmethod
    def require_source_id(cls, value: Any) -> str:
        return _exact_str(value, "invalid raw receipt projection fixture")

    @field_validator("market_date", mode="before")
    @classmethod
    def parse_market_date(cls, value: Any) -> dt.date:
        if type(value) is not str:
            raise ValueError("invalid raw receipt projection fixture")
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError:
            raise ValueError("invalid raw receipt projection fixture") from None
        if parsed.isoformat() != value:
            raise ValueError("invalid raw receipt projection fixture")
        return parsed

    @field_validator("receipts", mode="before")
    @classmethod
    def require_fixture_receipts(cls, value: Any) -> tuple[Any, ...]:
        if type(value) is not list:
            raise ValueError("invalid raw receipt projection fixture")
        return tuple(value)

    @model_validator(mode="after")
    def validate_fixture(self) -> Self:
        if (
            _SOURCE_ID.fullmatch(self.source_id) is None
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


def _exact_str(value: Any, message: str) -> str:
    if type(value) is not str:
        raise ValueError(message)
    return value


def _exact_int(value: Any, message: str) -> int:
    if type(value) is not int:
        raise ValueError(message)
    return value


def _exact_date(value: Any, message: str, info: ValidationInfo) -> dt.date:
    if info.mode == "json":
        if type(value) is not str:
            raise ValueError(message)
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError:
            raise ValueError(message) from None
        if parsed.isoformat() != value:
            raise ValueError(message)
        return parsed
    if type(value) is not dt.date:
        raise ValueError(message)
    return value


def _exact_datetime(value: Any, message: str, info: ValidationInfo) -> dt.datetime:
    if info.mode == "json":
        if type(value) is not str:
            raise ValueError(message)
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(message) from None
        if not _aware(parsed) or _canonical_json_timestamp(parsed) != value:
            raise ValueError(message)
        return parsed
    if type(value) is not dt.datetime:
        raise ValueError(message)
    return value


def _canonical_json_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
