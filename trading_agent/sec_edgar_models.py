from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Self, override

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

_CIK = re.compile(r"^[0-9]{10}$")
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_FORM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./-]{0,31}$")
_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_CONTENT_ENCODING = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
SEC_EDGAR_MAX_RAW_BYTES: Final = 64 * 1024 * 1024


class SecEdgarResponseError(ValueError):
    __slots__ = ("failure_code",)

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code

    @override
    def __str__(self) -> str:
        return f"SEC EDGAR response is invalid: {self.failure_code}"


@dataclass(frozen=True, slots=True)
class SecSubmissionRawResponse:
    collection_id: str
    cik: str
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)
    content_encoding: str = "identity"

    def __post_init__(self) -> None:
        if (
            _SAFE_ID.fullmatch(self.collection_id) is None
            or _CIK.fullmatch(self.cik) is None
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or _CONTENT_ENCODING.fullmatch(self.content_encoding) is None
            or len(self.raw_payload) > SEC_EDGAR_MAX_RAW_BYTES
        ):
            raise SecEdgarResponseError("raw_response")
        object.__setattr__(self, "received_at", self.received_at.astimezone(dt.UTC))

    @property
    def receipt_id(self) -> str:
        material = "|".join(
            (
                self.collection_id,
                self.cik,
                self.received_at.isoformat(),
                str(self.status_code),
                self.content_type,
                self.content_encoding,
                hashlib.sha256(self.raw_payload).hexdigest(),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class SecCollectionStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class SecSubmissionRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    collection_id: str
    cik: str
    started_at: dt.datetime
    completed_at: dt.datetime
    status: SecCollectionStatus
    failure_code: str | None
    receipt_id: str | None
    filing_count: int = Field(ge=0)
    additional_history_file_count: int = Field(ge=0)

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        success = self.status is SecCollectionStatus.SUCCESS
        if (
            _SAFE_ID.fullmatch(self.collection_id) is None
            or _CIK.fullmatch(self.cik) is None
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or (self.receipt_id is not None and _HEX64.fullmatch(self.receipt_id) is None)
            or success != (self.failure_code is None)
            or (success and self.receipt_id is None)
            or (
                not success
                and (self.receipt_id is None) != (self.failure_code == "transport")
            )
            or (not success and self.filing_count != 0)
            or (not success and self.additional_history_file_count != 0)
            or (self.failure_code is not None and _SAFE_ID.fullmatch(self.failure_code) is None)
        ):
            raise SecEdgarResponseError("run")
        return self

    @property
    def run_id(self) -> str:
        return hashlib.sha256(f"{self.collection_id}|{self.cik}".encode()).hexdigest()


class SecFilingEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    cik: str
    accession_number: str
    form: str
    filing_date: dt.date
    report_date: dt.date | None
    accepted_at: dt.datetime
    primary_document: str
    primary_document_description: str
    items: tuple[str, ...]
    size_bytes: int = Field(ge=0)
    is_xbrl: bool
    is_inline_xbrl: bool

    @field_validator("accepted_at")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if (
            _CIK.fullmatch(self.cik) is None
            or _ACCESSION.fullmatch(self.accession_number) is None
            or _FORM.fullmatch(self.form) is None
            or not _aware(self.accepted_at)
            or _DOCUMENT.fullmatch(self.primary_document) is None
            or not _bounded_optional_text(self.primary_document_description, 2_000)
            or any(not _bounded_text(item, 64) for item in self.items)
            or len(self.items) != len(set(self.items))
        ):
            raise SecEdgarResponseError("filing")
        return self

    @property
    def event_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


class SecSubmissionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    cik: str
    filings: tuple[SecFilingEvent, ...]
    additional_history_file_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if (
            _CIK.fullmatch(self.cik) is None
            or any(item.cik != self.cik for item in self.filings)
            or len(tuple(item.accession_number for item in self.filings))
            != len(set(item.accession_number for item in self.filings))
        ):
            raise SecEdgarResponseError("snapshot")
        return self


def normalize_sec_cik(value: str) -> str:
    if _CIK.fullmatch(value) is None:
        raise SecEdgarResponseError("cik")
    return value


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _bounded_text(value: str, maximum: int) -> bool:
    return value == value.strip() and 0 < len(value) <= maximum and all(character >= " " for character in value)


def _bounded_optional_text(value: str, maximum: int) -> bool:
    return value == value.strip() and len(value) <= maximum and all(character >= " " for character in value)
