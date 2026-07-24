from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

KIS_FUTURES_MAX_RAW_BYTES = 1024 * 1024
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_CODE = re.compile(r"^[A-Z0-9]{1,12}$")
_EXCHANGE = re.compile(r"^[A-Z0-9]{2,12}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class KisFuturesQuoteStatus(StrEnum):
    FAILED = "failed"
    SUCCESS = "success"


class KisFuturesQuoteFailure(StrEnum):
    HTTP_STATUS = "http_status"
    PROVIDER_STATUS = "provider_status"
    RESPONSE_STRUCTURE = "response_structure"
    TRANSPORT = "transport"


class KisFuturesQuoteError(ValueError):
    __slots__ = ("failure",)

    failure: KisFuturesQuoteFailure

    def __init__(self, failure: KisFuturesQuoteFailure) -> None:
        super().__init__()
        self.failure = failure

    @override
    def __str__(self) -> str:
        return "KIS overseas futures quote is invalid"


class KisFuturesQuoteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    root_symbol: str
    symbols: tuple[str, ...] = Field(min_length=2, max_length=8)

    @field_validator("symbols", mode="before")
    @classmethod
    def canonical_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _CODE.fullmatch(self.root_symbol) is None
            or any(
                _CODE.fullmatch(symbol) is None
                or not symbol.startswith(self.root_symbol)
                for symbol in self.symbols
            )
        ):
            raise ValueError("invalid KIS futures quote request")
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class KisFuturesQuoteRawResponse:
    request_id: str
    symbol: str
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"[0-9a-f]{64}", self.request_id) is None
            or _CODE.fullmatch(self.symbol) is None
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or not self.raw_payload
            or len(self.raw_payload) > KIS_FUTURES_MAX_RAW_BYTES
        ):
            raise ValueError("invalid KIS futures raw response")

    @property
    def receipt_id(self) -> str:
        material = "|".join(
            (
                self.request_id,
                self.symbol,
                self.received_at.isoformat(),
                str(self.status_code),
                self.content_type,
                hashlib.sha256(self.raw_payload).hexdigest(),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class KisFuturesQuote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    symbol: str
    exchange: str
    currency: str
    received_at: dt.datetime
    provider_process_date: dt.date
    provider_process_time: dt.time
    business_date: dt.date
    listing_date: dt.date
    expiration_date: dt.date
    last_trade_date: dt.date
    last_price: Decimal
    bid_price: Decimal
    ask_price: Decimal
    previous_close: Decimal
    settlement_price: Decimal | None
    accumulated_volume: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        prices = (
            self.last_price,
            self.bid_price,
            self.ask_price,
            self.previous_close,
        )
        if (
            _CODE.fullmatch(self.symbol) is None
            or _EXCHANGE.fullmatch(self.exchange) is None
            or _CURRENCY.fullmatch(self.currency) is None
            or not _aware(self.received_at)
            or any(not value.is_finite() or value <= 0 for value in prices)
            or self.bid_price > self.ask_price
            or (
                self.settlement_price is not None
                and (
                    not self.settlement_price.is_finite()
                    or self.settlement_price <= 0
                )
            )
            or self.listing_date > self.last_trade_date
            or self.last_trade_date > self.expiration_date
            or self.business_date > self.expiration_date
        ):
            raise KisFuturesQuoteError(
                KisFuturesQuoteFailure.RESPONSE_STRUCTURE
            )
        return self


class KisFuturesQuoteRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: KisFuturesQuoteRequest
    started_at: dt.datetime
    completed_at: dt.datetime
    status: KisFuturesQuoteStatus
    failure: KisFuturesQuoteFailure | None
    receipt_ids: tuple[str, ...]
    quotes: tuple[KisFuturesQuote, ...]

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        symbols = tuple(item.symbol for item in self.quotes)
        success = (
            self.status is KisFuturesQuoteStatus.SUCCESS
            and self.failure is None
            and symbols == self.request.symbols
            and len(self.receipt_ids) == len(self.request.symbols)
        )
        failed = (
            self.status is KisFuturesQuoteStatus.FAILED
            and self.failure is not None
            and not self.quotes
            and len(self.receipt_ids) <= len(self.request.symbols)
        )
        if (
            not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.started_at > self.completed_at
            or len(set(self.receipt_ids)) != len(self.receipt_ids)
            or not (success or failed)
        ):
            raise KisFuturesQuoteError(
                KisFuturesQuoteFailure.RESPONSE_STRUCTURE
            )
        return self

    @property
    def run_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "KIS_FUTURES_MAX_RAW_BYTES",
    "KisFuturesQuote",
    "KisFuturesQuoteError",
    "KisFuturesQuoteFailure",
    "KisFuturesQuoteRawResponse",
    "KisFuturesQuoteRequest",
    "KisFuturesQuoteRun",
    "KisFuturesQuoteStatus",
)
