from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_option_chain_provider_models import (
    OptionBar,
    OptionGreeks,
    OptionQuote,
    OptionTrade,
    ProviderOptionChainPage,
    ProviderOptionSnapshot,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

ALPACA_OPTION_CHAIN_MAX_RAW_BYTES: Final = 16 * 1024 * 1024
_COLLECTION_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_UNDERLYING = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_OPTION_SYMBOL = re.compile(r"^([A-Z]{1,6})([0-9]{6})([CP])([0-9]{8})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$"
)


class OptionFeed(StrEnum):
    INDICATIVE = "indicative"
    OPRA = "opra"


class OptionContractType(StrEnum):
    CALL = "call"
    PUT = "put"


class OptionChainStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class OptionChainFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"
    PAGE_LIMIT = "page_limit"
    TOKEN_CYCLE = "token_cycle"
    DUPLICATE_CONTRACT = "duplicate_contract"


class AlpacaOptionChainError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca option chain state is invalid"


class OptionChainRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    underlying_symbol: str
    feed: OptionFeed
    expiration_date: dt.date
    contract_type: OptionContractType
    limit: int = Field(ge=1, le=1_000)
    max_pages: int = Field(ge=1, le=8)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _COLLECTION_ID.fullmatch(self.collection_id) is None
            or _UNDERLYING.fullmatch(self.underlying_symbol) is None
        ):
            raise AlpacaOptionChainError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class OptionChainRawResponse:
    request_id: str
    page_index: int
    page_token: str | None
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            _SHA256.fullmatch(self.request_id) is None
            or not 0 <= self.page_index < 8
            or not _token(self.page_token)
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or type(self.raw_payload) is not bytes
            or len(self.raw_payload) > ALPACA_OPTION_CHAIN_MAX_RAW_BYTES
        ):
            raise AlpacaOptionChainError

    @property
    def receipt_id(self) -> str:
        material = "|".join(
            (
                self.request_id,
                str(self.page_index),
                self.page_token or "",
                self.received_at.astimezone(dt.UTC).isoformat(),
                str(self.status_code),
                hashlib.sha256(self.raw_payload).hexdigest(),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class OptionContractSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    underlying_symbol: str
    expiration_date: dt.date
    contract_type: OptionContractType
    strike_price: Decimal
    latest_quote: OptionQuote | None
    latest_trade: OptionTrade | None
    implied_volatility: Decimal | None
    greeks: OptionGreeks | None
    minute_bar: OptionBar | None = None
    daily_bar: OptionBar | None = None
    previous_daily_bar: OptionBar | None = None


class OptionChainRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: OptionChainRequest
    started_at: dt.datetime
    completed_at: dt.datetime
    status: OptionChainStatus
    failure_code: OptionChainFailure | None
    receipt_ids: tuple[str, ...] = Field(max_length=8)
    snapshots: tuple[OptionContractSnapshot, ...] = Field(max_length=8_000)

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        match self.status:
            case OptionChainStatus.SUCCESS:
                variant_valid = self.failure_code is None and bool(self.receipt_ids)
            case OptionChainStatus.FAILED:
                variant_valid = self.failure_code is not None
            case unreachable:
                assert_never(unreachable)
        symbols = tuple(item.symbol for item in self.snapshots)
        if (
            not variant_valid
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or len(self.receipt_ids) > self.request.max_pages
            or any(_SHA256.fullmatch(value) is None for value in self.receipt_ids)
            or len(self.receipt_ids) != len(set(self.receipt_ids))
            or symbols != tuple(sorted(set(symbols)))
            or any(
                item.underlying_symbol != self.request.underlying_symbol
                or item.expiration_date != self.request.expiration_date
                or item.contract_type is not self.request.contract_type
                for item in self.snapshots
            )
        ):
            raise AlpacaOptionChainError
        return self

    @property
    def run_id(self) -> str:
        return hashlib.sha256(
            f"alpaca-option-chain|{self.request.request_id}".encode()
        ).hexdigest()


def option_snapshot(
    symbol: str,
    value: ProviderOptionSnapshot,
) -> OptionContractSnapshot:
    matched = _OPTION_SYMBOL.fullmatch(symbol)
    if matched is None:
        raise AlpacaOptionChainError
    underlying, date_text, right, strike_text = matched.groups()
    contract_type = (
        OptionContractType.CALL if right == "C" else OptionContractType.PUT
    )
    return OptionContractSnapshot(
        symbol=symbol,
        underlying_symbol=underlying,
        expiration_date=dt.datetime.strptime(date_text, "%y%m%d").date(),
        contract_type=contract_type,
        strike_price=Decimal(strike_text) / Decimal(1_000),
        latest_quote=value.latest_quote,
        latest_trade=value.latest_trade,
        implied_volatility=value.implied_volatility,
        greeks=value.greeks,
        minute_bar=value.minute_bar,
        daily_bar=value.daily_bar,
        previous_daily_bar=value.previous_daily_bar,
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _token(value: str | None) -> bool:
    return value is None or (
        0 < len(value) <= 2_048
        and not any(character < " " for character in value)
    )


__all__ = (
    "ALPACA_OPTION_CHAIN_MAX_RAW_BYTES",
    "AlpacaOptionChainError",
    "OptionBar",
    "OptionChainFailure",
    "OptionChainRawResponse",
    "OptionChainRequest",
    "OptionChainRun",
    "OptionChainStatus",
    "OptionContractSnapshot",
    "OptionContractType",
    "OptionFeed",
    "OptionGreeks",
    "OptionQuote",
    "OptionTrade",
    "ProviderOptionChainPage",
    "ProviderOptionSnapshot",
    "option_snapshot",
)
