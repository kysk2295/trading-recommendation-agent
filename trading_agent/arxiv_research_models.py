from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

ARXIV_MAX_RAW_BYTES: Final = 2 * 1024 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_CATEGORY = re.compile(r"^[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?$")
_ARXIV_ID = re.compile(r"^(?:[0-9]{4}\.[0-9]{4,5}|[a-z-]+(?:\.[A-Z]{2})?/[0-9]{7})v[1-9][0-9]*$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class ArxivResearchError(ValueError):
    @override
    def __str__(self) -> str:
        return "arXiv research evidence is invalid"


class ArxivRunStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class ArxivFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"


class ArxivResearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    category: str
    terms: tuple[str, ...] = Field(min_length=1, max_length=5)
    max_results: int = Field(ge=1, le=10)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _ID.fullmatch(self.collection_id) is None
            or _CATEGORY.fullmatch(self.category) is None
            or self.terms != tuple(sorted(set(self.terms)))
            or any(not _canonical_text(term, 80) for term in self.terms)
        ):
            raise ArxivResearchError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


class ArxivRawReceipt(BaseModel):
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
            raise ArxivResearchError from None
        if (
            _SHA.fullmatch(self.request_id) is None
            or not _aware(self.received_at)
            or self.content_type not in {"application/atom+xml", "application/xml", "text/xml"}
            or _SHA.fullmatch(self.payload_sha256) is None
            or not 1 <= len(raw) <= ARXIV_MAX_RAW_BYTES
            or hashlib.sha256(raw).hexdigest() != self.payload_sha256
        ):
            raise ArxivResearchError
        return self

    @property
    def raw_payload(self) -> bytes:
        return base64.b64decode(self.payload_base64, validate=True)

    @property
    def receipt_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()

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


class ArxivPaper(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    arxiv_id: str
    title: str
    summary: str
    authors: tuple[str, ...] = Field(min_length=1, max_length=64)
    categories: tuple[str, ...] = Field(min_length=1, max_length=32)
    published_at: dt.datetime
    updated_at: dt.datetime
    abstract_url: str
    doi: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_paper(self) -> Self:
        if (
            _ARXIV_ID.fullmatch(self.arxiv_id) is None
            or not _canonical_text(self.title, 2_000)
            or not _canonical_text(self.summary, 20_000)
            or any(not _canonical_text(author, 512) for author in self.authors)
            or self.categories != tuple(sorted(set(self.categories)))
            or any(_CATEGORY.fullmatch(category) is None for category in self.categories)
            or not _aware(self.published_at)
            or not _aware(self.updated_at)
            or self.updated_at < self.published_at
            or self.abstract_url != f"https://arxiv.org/abs/{self.arxiv_id}"
            or (self.doi is not None and not _canonical_text(self.doi, 256))
        ):
            raise ArxivResearchError
        return self


class ArxivResearchSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    raw_receipt_id: str
    observed_at: dt.datetime
    category: str
    terms: tuple[str, ...]
    total_results: int = Field(ge=0)
    papers: tuple[ArxivPaper, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        ids = tuple(paper.arxiv_id for paper in self.papers)
        if (
            _SHA.fullmatch(self.request_id) is None
            or _SHA.fullmatch(self.raw_receipt_id) is None
            or not _aware(self.observed_at)
            or _CATEGORY.fullmatch(self.category) is None
            or self.terms != tuple(sorted(set(self.terms)))
            or ids != tuple(sorted(set(ids)))
            or self.total_results < len(self.papers)
        ):
            raise ArxivResearchError
        return self

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


class ArxivTerminal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: ArxivResearchRequest
    completed_at: dt.datetime
    status: ArxivRunStatus
    failure: ArxivFailure | None
    receipt_id: str | None
    snapshot: ArxivResearchSnapshot | None

    @model_validator(mode="after")
    def validate_terminal(self) -> Self:
        success = self.status is ArxivRunStatus.SUCCESS
        if (
            not _aware(self.completed_at)
            or success != (self.failure is None and self.receipt_id is not None and self.snapshot is not None)
            or (
                not success
                and (
                    self.failure is None
                    or self.snapshot is not None
                    or (self.failure is ArxivFailure.TRANSPORT) != (self.receipt_id is None)
                )
            )
            or (
                self.snapshot is not None
                and (
                    self.snapshot.request_id != self.request.request_id
                    or self.snapshot.raw_receipt_id != self.receipt_id
                    or self.snapshot.category != self.request.category
                    or self.snapshot.terms != self.request.terms
                )
            )
        ):
            raise ArxivResearchError
        return self


def _canonical_text(value: str, maximum: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and all(character >= " " for character in value)
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "ARXIV_MAX_RAW_BYTES",
    "ArxivFailure",
    "ArxivPaper",
    "ArxivRawReceipt",
    "ArxivResearchError",
    "ArxivResearchRequest",
    "ArxivResearchSnapshot",
    "ArxivRunStatus",
    "ArxivTerminal",
)
