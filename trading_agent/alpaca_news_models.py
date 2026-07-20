from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, Self, assert_never, override
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

ALPACA_NEWS_MAX_RAW_BYTES: Final = 8 * 1024 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_CONTENT_ENCODING = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")


class AlpacaNewsContractError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca news contract is invalid"


class AlpacaNewsRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    symbols: tuple[str, ...] = Field(min_length=1, max_length=50)
    start_at: dt.datetime
    end_at: dt.datetime
    limit: int = Field(ge=1, le=50)
    max_pages: int = Field(ge=1, le=8)

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @field_validator("start_at", "end_at")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _SAFE_ID.fullmatch(self.collection_id) is None
            or any(_SYMBOL.fullmatch(symbol) is None for symbol in self.symbols)
            or not _aware(self.start_at)
            or not _aware(self.end_at)
            or self.start_at >= self.end_at
            or self.end_at - self.start_at > dt.timedelta(days=1)
        ):
            raise AlpacaNewsContractError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AlpacaNewsRawResponse:
    request_id: str
    page_index: int
    page_token: str | None
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)
    content_encoding: str = "identity"

    def __post_init__(self) -> None:
        if (
            _HEX64.fullmatch(self.request_id) is None
            or not 0 <= self.page_index < 8
            or not _valid_token(self.page_token)
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or _CONTENT_ENCODING.fullmatch(self.content_encoding) is None
            or type(self.raw_payload) is not bytes
            or len(self.raw_payload) > ALPACA_NEWS_MAX_RAW_BYTES
        ):
            raise AlpacaNewsContractError
        object.__setattr__(self, "received_at", self.received_at.astimezone(dt.UTC))

    @property
    def receipt_id(self) -> str:
        material = "|".join(
            (
                self.request_id,
                str(self.page_index),
                self.page_token or "",
                self.received_at.isoformat(),
                str(self.status_code),
                self.content_type,
                self.content_encoding,
                hashlib.sha256(self.raw_payload).hexdigest(),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class AlpacaNewsArticle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    provider_article_id: int = Field(gt=0)
    headline: str = Field(min_length=1, max_length=1_000, repr=False)
    source: str
    symbols: tuple[str, ...] = Field(min_length=1, max_length=64)
    created_at: dt.datetime
    updated_at: dt.datetime
    url: str

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if _aware(value) else value

    @model_validator(mode="after")
    def validate_article(self) -> Self:
        url = urlsplit(self.url)
        if (
            self.headline != self.headline.strip()
            or any(character < " " for character in self.headline)
            or _SOURCE.fullmatch(self.source) is None
            or any(_SYMBOL.fullmatch(symbol) is None for symbol in self.symbols)
            or not _aware(self.created_at)
            or not _aware(self.updated_at)
            or self.created_at > self.updated_at
            or url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.fragment
        ):
            raise AlpacaNewsContractError
        return self

    @property
    def event_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


class AlpacaNewsPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    articles: tuple[AlpacaNewsArticle, ...] = Field(max_length=50)
    next_page_token: str | None

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        identities = tuple(article.provider_article_id for article in self.articles)
        if len(identities) != len(set(identities)) or not _valid_token(self.next_page_token):
            raise AlpacaNewsContractError
        return self


class AlpacaNewsRunStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class AlpacaNewsFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"
    PAGE_LIMIT = "page_limit"
    TOKEN_CYCLE = "token_cycle"
    DUPLICATE_ARTICLE = "duplicate_article"


class AlpacaNewsRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: AlpacaNewsRequest = Field(repr=False)
    started_at: dt.datetime
    completed_at: dt.datetime
    status: AlpacaNewsRunStatus
    failure_code: AlpacaNewsFailure | None
    receipt_ids: tuple[str, ...] = Field(max_length=8)
    page_count: int = Field(ge=0, le=8)
    article_count: int = Field(ge=0, le=400)
    latest_event_at: dt.datetime | None

    @field_validator("started_at", "completed_at", "latest_event_at")
    @classmethod
    def normalize_optional_time(cls, value: dt.datetime | None) -> dt.datetime | None:
        return value.astimezone(dt.UTC) if value is not None and _aware(value) else value

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        match self.status:
            case AlpacaNewsRunStatus.SUCCESS:
                variant_valid = self.failure_code is None and bool(self.receipt_ids)
            case AlpacaNewsRunStatus.FAILED:
                variant_valid = self.failure_code is not None
            case unreachable:
                assert_never(unreachable)
        if (
            not variant_valid
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or self.page_count != len(self.receipt_ids)
            or self.page_count > self.request.max_pages
            or any(_HEX64.fullmatch(identity) is None for identity in self.receipt_ids)
            or len(self.receipt_ids) != len(set(self.receipt_ids))
            or (self.article_count == 0) != (self.latest_event_at is None)
            or (self.latest_event_at is not None and not _aware(self.latest_event_at))
            or self.article_count > self.page_count * self.request.limit
            or (
                self.failure_code is not None
                and self.failure_code is not AlpacaNewsFailure.TRANSPORT
                and not self.receipt_ids
            )
        ):
            raise AlpacaNewsContractError
        return self

    @property
    def run_id(self) -> str:
        return hashlib.sha256(f"alpaca-news-run|{self.request.request_id}".encode()).hexdigest()


def _valid_token(value: str | None) -> bool:
    return value is None or (0 < len(value) <= 2_048 and not any(character < " " for character in value))


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
