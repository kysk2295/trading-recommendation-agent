from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, override

from trading_agent.sec_edgar_models import SEC_EDGAR_MAX_RAW_BYTES

_CIK = re.compile(r"^[0-9]{10}$")
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_CONTENT_ENCODING = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
SEC_FILING_DOCUMENT_MAX_RAW_BYTES: Final = SEC_EDGAR_MAX_RAW_BYTES


class InvalidSecFilingDocumentTargetError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC filing document target is invalid"


class InvalidSecFilingDocumentResponseError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC filing document response is invalid"


class InvalidSecFilingDocumentRunError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC filing document run is invalid"


@dataclass(frozen=True, slots=True)
class SecFilingDocumentTarget:
    source_version_id: str
    source_receipt_id: str
    cik: str
    accession_number: str
    primary_document: str = field(repr=False)
    accepted_at: dt.datetime
    observed_at: dt.datetime

    def __post_init__(self) -> None:
        if (
            _HEX64.fullmatch(self.source_version_id) is None
            or _HEX64.fullmatch(self.source_receipt_id) is None
            or _CIK.fullmatch(self.cik) is None
            or self.cik == "0000000000"
            or _ACCESSION.fullmatch(self.accession_number) is None
            or _DOCUMENT.fullmatch(self.primary_document) is None
            or not _aware(self.accepted_at)
            or not _aware(self.observed_at)
            or self.accepted_at > self.observed_at
        ):
            raise InvalidSecFilingDocumentTargetError
        object.__setattr__(self, "accepted_at", self.accepted_at.astimezone(dt.UTC))
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(dt.UTC))

    @property
    def archive_path(self) -> str:
        issuer_folder = str(int(self.cik))
        accession_folder = self.accession_number.replace("-", "")
        return f"/Archives/edgar/data/{issuer_folder}/{accession_folder}/{self.primary_document}"

    @property
    def target_id(self) -> str:
        material = "|".join(
            (
                self.source_version_id,
                self.source_receipt_id,
                self.cik,
                self.accession_number,
                self.primary_document,
                self.accepted_at.isoformat(),
                self.observed_at.isoformat(),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SecFilingDocumentRawResponse:
    target_id: str
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)
    content_encoding: str = "identity"

    def __post_init__(self) -> None:
        if (
            _HEX64.fullmatch(self.target_id) is None
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or _CONTENT_ENCODING.fullmatch(self.content_encoding) is None
            or not isinstance(self.raw_payload, bytes)
            or len(self.raw_payload) > SEC_FILING_DOCUMENT_MAX_RAW_BYTES
        ):
            raise InvalidSecFilingDocumentResponseError
        object.__setattr__(self, "received_at", self.received_at.astimezone(dt.UTC))

    @property
    def receipt_id(self) -> str:
        material = "|".join(
            (
                self.target_id,
                self.received_at.isoformat(),
                str(self.status_code),
                self.content_type,
                self.content_encoding,
                hashlib.sha256(self.raw_payload).hexdigest(),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class SecFilingDocumentStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SecFilingDocumentRun:
    target: SecFilingDocumentTarget = field(repr=False)
    started_at: dt.datetime
    completed_at: dt.datetime
    status: SecFilingDocumentStatus
    failure_code: str | None
    receipt_id: str | None
    byte_count: int

    def __post_init__(self) -> None:
        success = self.status is SecFilingDocumentStatus.SUCCESS
        if (
            not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or success != (self.failure_code is None)
            or (success and self.receipt_id is None)
            or (self.receipt_id is not None and _HEX64.fullmatch(self.receipt_id) is None)
            or self.failure_code not in {None, "transport", "http_status", "empty_payload"}
            or (self.failure_code == "transport") != (self.receipt_id is None)
            or not 0 <= self.byte_count <= SEC_FILING_DOCUMENT_MAX_RAW_BYTES
            or (self.receipt_id is None and self.byte_count != 0)
        ):
            raise InvalidSecFilingDocumentRunError
        object.__setattr__(self, "started_at", self.started_at.astimezone(dt.UTC))
        object.__setattr__(self, "completed_at", self.completed_at.astimezone(dt.UTC))

    @property
    def run_id(self) -> str:
        return hashlib.sha256(f"sec-filing-document-run|{self.target.target_id}".encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
