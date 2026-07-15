from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrCoverageStatus,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FAILURE_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class KrSourceReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_run_id: str
    source: KrCatalystSource
    request_key: str
    received_at: dt.datetime
    http_status: int
    content_type: str
    payload_sha256: str

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if (
            _SAFE_ID.fullmatch(self.source_run_id) is None
            or _SAFE_ID.fullmatch(self.request_key) is None
            or not _aware(self.received_at)
            or not 100 <= self.http_status <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or _SHA256.fullmatch(self.payload_sha256) is None
        ):
            raise ValueError("invalid KR source receipt")
        return self

    @property
    def receipt_id(self) -> str:
        return _identity_hash(
            self.source_run_id,
            self.source.value,
            self.request_key,
        )


class KrCatalystObservationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_cycle_id: str
    catalyst_id: str
    receipt_id: str
    item_index: int
    item_payload_sha256: str

    @model_validator(mode="after")
    def validate_link(self) -> Self:
        if (
            _SAFE_ID.fullmatch(self.collection_cycle_id) is None
            or _SHA256.fullmatch(self.catalyst_id) is None
            or _SHA256.fullmatch(self.receipt_id) is None
            or self.item_index < 0
            or _SHA256.fullmatch(self.item_payload_sha256) is None
        ):
            raise ValueError("invalid KR catalyst receipt lineage")
        return self


class KrSourceCollectionRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_run_id: str
    collection_cycle_id: str
    source: KrCatalystSource
    adapter_version: str
    started_at: dt.datetime
    completed_at: dt.datetime
    status: KrCoverageStatus
    record_count: int
    failure_code: str | None = None
    receipt_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        failure_valid = (
            self.failure_code is None
            if self.status is KrCoverageStatus.SUCCESS
            else self.failure_code is not None
            and _FAILURE_CODE.fullmatch(self.failure_code) is not None
        )
        if (
            _SAFE_ID.fullmatch(self.source_run_id) is None
            or _SAFE_ID.fullmatch(self.collection_cycle_id) is None
            or _SAFE_ID.fullmatch(self.adapter_version) is None
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or self.record_count < 0
            or not failure_valid
            or any(_SHA256.fullmatch(item) is None for item in self.receipt_ids)
            or self.receipt_ids != tuple(sorted(set(self.receipt_ids)))
        ):
            raise ValueError("invalid KR source collection run")
        return self


@dataclass(frozen=True, slots=True)
class StoredKrSourceReceipt:
    receipt: KrSourceReceipt
    raw_payload: bytes = field(repr=False)


def _identity_hash(*parts: str) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
