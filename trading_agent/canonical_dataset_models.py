from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from typing import Any, Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from trading_agent.canonical_event_models import CanonicalEventEnvelope
from trading_agent.data_capability_models import DataSourceId
from trading_agent.raw_object_manifest_models import RawObjectPartitionManifest
from trading_agent.security_master_models import DataMarketDomain

_EVENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_SENSITIVE_LINEAGE_KEYS = frozenset(
    {"payload", "payload_base64", "raw_payload", "raw_payload_base64", "account_id", "request_key"}
)


class InvalidCanonicalDatasetBatchError(ValueError):
    def __init__(self, *_args: object) -> None:
        super().__init__("canonical dataset batch is invalid")

    @override
    def __str__(self) -> str:
        return "canonical dataset batch is invalid"

    @override
    def __repr__(self) -> str:
        return "InvalidCanonicalDatasetBatchError()"


class CanonicalDatasetPartition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    source_id: DataSourceId
    market_domain: DataMarketDomain
    event_type: str
    market_date: dt.date
    canonical_event_schema_version: int = 1

    @field_validator("schema_version", "canonical_event_schema_version", mode="before")
    @classmethod
    def require_schema_version_int(cls, value: Any) -> int:
        if type(value) is not int:
            raise ValueError("invalid canonical dataset partition")
        return value

    @field_validator("event_type", mode="before")
    @classmethod
    def require_event_type(cls, value: Any) -> str:
        if type(value) is not str:
            raise ValueError("invalid canonical dataset partition")
        return value

    @field_validator("market_date", mode="before")
    @classmethod
    def require_market_date(cls, value: Any) -> dt.date:
        if type(value) is not dt.date:
            raise ValueError("invalid canonical dataset partition")
        return value

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        if (
            not _data_source_is_valid(self.source_id)
            or self.canonical_event_schema_version != 1
            or _EVENT_TYPE.fullmatch(self.event_type) is None
        ):
            raise ValueError("invalid canonical dataset partition")
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(dict(copied.__dict__))


class CanonicalDatasetBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True, strict=True)

    schema_version: Literal[1] = 1
    partition: CanonicalDatasetPartition
    raw_manifest: RawObjectPartitionManifest
    events: tuple[CanonicalEventEnvelope, ...]

    @model_validator(mode="before")
    @classmethod
    def reject_sensitive_lineage_input(cls, values: Any) -> Any:
        _raise_if_untrusted_lineage_input(values)
        return values

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_schema_version_int(cls, value: Any) -> int:
        if type(value) is not int:
            raise ValueError("invalid canonical dataset batch")
        return value

    @field_validator("raw_manifest", mode="before")
    @classmethod
    def require_exact_raw_manifest(cls, value: Any) -> RawObjectPartitionManifest:
        if type(value) is not RawObjectPartitionManifest:
            raise ValueError("invalid canonical dataset batch")
        return value

    @field_validator("events", mode="before")
    @classmethod
    def require_exact_events(cls, value: Any) -> tuple[CanonicalEventEnvelope, ...]:
        if type(value) is not tuple or any(type(event) is not CanonicalEventEnvelope for event in value):
            raise ValueError("invalid canonical dataset batch")
        return value

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if not _partition_is_valid(self.partition) or not _raw_manifest_is_valid(self.raw_manifest) or any(
            not _event_is_valid(event) for event in self.events
        ):
            raise ValueError("invalid canonical dataset batch")

        event_ids = tuple(event.event_id for event in self.events)
        receipt_ids = {receipt.receipt_id for receipt in self.raw_manifest.receipts}
        if (
            not self.events
            or event_ids != tuple(sorted(set(event_ids)))
            or self.raw_manifest.market_date != self.partition.market_date
            or any(
                event.source_id != self.partition.source_id
                or event.event_type != self.partition.event_type
                or event.schema_version != self.partition.canonical_event_schema_version
                or event.raw_receipt_ref not in receipt_ids
                for event in self.events
            )
        ):
            raise ValueError("invalid canonical dataset batch")
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(dict(copied.__dict__))


_MISSING = object()


def _raise_if_untrusted_lineage_input(values: object) -> None:
    if not isinstance(values, Mapping):
        return
    raw_manifest = values.get("raw_manifest", _MISSING)
    events = values.get("events", _MISSING)
    if raw_manifest is not _MISSING and _contains_sensitive_lineage_material(raw_manifest):
        raise InvalidCanonicalDatasetBatchError
    if events is not _MISSING and _contains_sensitive_lineage_material(events):
        raise InvalidCanonicalDatasetBatchError


def _contains_sensitive_lineage_material(value: object) -> bool:
    if type(value) in {bytes, bytearray, memoryview}:
        return True
    if isinstance(value, Mapping):
        return any(
            (type(key) is str and key in _SENSITIVE_LINEAGE_KEYS) or _contains_sensitive_lineage_material(item)
            for key, item in value.items()
        )
    if isinstance(value, BaseModel):
        return _contains_sensitive_lineage_material(value.__dict__)
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_sensitive_lineage_material(item) for item in value)
    return False


def _data_source_is_valid(source: DataSourceId) -> bool:
    try:
        DataSourceId.model_validate(source.model_dump(mode="python"))
    except ValidationError:
        return False
    return type(source.schema_version) is int and source.schema_version == 1


def _partition_is_valid(partition: CanonicalDatasetPartition) -> bool:
    try:
        CanonicalDatasetPartition.model_validate(partition.model_dump(mode="python"))
    except ValidationError:
        return False
    return (
        type(partition.schema_version) is int
        and partition.schema_version == 1
        and type(partition.canonical_event_schema_version) is int
        and partition.canonical_event_schema_version == 1
    )


def _raw_manifest_is_valid(manifest: RawObjectPartitionManifest) -> bool:
    try:
        RawObjectPartitionManifest.model_validate(manifest.model_dump(mode="python"))
    except ValidationError:
        return False
    return type(manifest.schema_version) is int and manifest.schema_version == 1


def _event_is_valid(event: CanonicalEventEnvelope) -> bool:
    try:
        CanonicalEventEnvelope.model_validate(event.model_dump(mode="python"))
    except ValidationError:
        return False
    return type(event.schema_version) is int and event.schema_version == 1


__all__ = (
    "CanonicalDatasetBatch",
    "CanonicalDatasetPartition",
    "InvalidCanonicalDatasetBatchError",
)
