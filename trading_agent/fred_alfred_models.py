from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

FRED_MAX_RAW_BYTES: Final = 4 * 1024 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SERIES = re.compile(r"^[A-Z][A-Z0-9_.-]{0,63}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class FredAlfredError(ValueError):
    @override
    def __str__(self) -> str:
        return "FRED/ALFRED evidence is invalid"


class FredSourceMode(StrEnum):
    FRED = "fred"
    ALFRED = "alfred"


class FredRunStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class FredFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"


class FredAlfredRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    source_mode: FredSourceMode
    series_id: str
    observation_start: dt.date
    observation_end: dt.date
    vintage_date: dt.date | None = None
    limit: int = Field(ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        vintage_required = self.source_mode is FredSourceMode.ALFRED
        if (
            _ID.fullmatch(self.collection_id) is None
            or _SERIES.fullmatch(self.series_id) is None
            or self.observation_start > self.observation_end
            or vintage_required != (self.vintage_date is not None)
        ):
            raise FredAlfredError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


class FredRawReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    received_at: dt.datetime
    status_code: int = Field(ge=100, le=599)
    content_type: str
    payload_sha256: str
    payload_base64: str = Field(repr=False)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        try:
            raw = base64.b64decode(self.payload_base64, validate=True)
        except (TypeError, ValueError):
            raise FredAlfredError from None
        if (
            _SHA.fullmatch(self.request_id) is None
            or not _aware(self.received_at)
            or self.content_type != "application/json"
            or _SHA.fullmatch(self.payload_sha256) is None
            or not 1 <= len(raw) <= FRED_MAX_RAW_BYTES
            or hashlib.sha256(raw).hexdigest() != self.payload_sha256
        ):
            raise FredAlfredError
        return self

    @property
    def raw_payload(self) -> bytes:
        return base64.b64decode(self.payload_base64, validate=True)

    @property
    def receipt_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()

    @classmethod
    def from_raw(
        cls,
        *,
        request_id: str,
        received_at: dt.datetime,
        status_code: int,
        content_type: str,
        raw_payload: bytes,
    ) -> Self:
        return cls(
            request_id=request_id,
            received_at=received_at,
            status_code=status_code,
            content_type=content_type,
            payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
            payload_base64=base64.b64encode(raw_payload).decode("ascii"),
        )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "FRED_MAX_RAW_BYTES",
    "FredAlfredError",
    "FredAlfredRequest",
    "FredFailure",
    "FredRawReceipt",
    "FredRunStatus",
    "FredSourceMode",
)
