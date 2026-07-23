from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_option_chain_models import OptionContractType
from trading_agent.alpaca_option_contract_provider_models import (
    OptionExerciseStyle,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.security_master_models import InstrumentAlias, InstrumentId

ALPACA_OPTION_CONTRACT_MAX_RAW_BYTES: Final = 16 * 1024 * 1024
_COLLECTION_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_UNDERLYING = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$"
)


class OptionCatalogStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class OptionCatalogFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"
    PAGE_LIMIT = "page_limit"
    TOKEN_CYCLE = "token_cycle"
    DUPLICATE_CONTRACT = "duplicate_contract"
    EMPTY_RESULT = "empty_result"


class AlpacaOptionContractError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca option contract state is invalid"


class OptionContractCatalogRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    underlying_symbol: str
    expiration_date: dt.date
    contract_type: OptionContractType
    limit: int = Field(ge=1, le=10_000)
    max_pages: int = Field(ge=1, le=8)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _COLLECTION_ID.fullmatch(self.collection_id) is None
            or _UNDERLYING.fullmatch(self.underlying_symbol) is None
        ):
            raise AlpacaOptionContractError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class OptionContractRawResponse:
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
            or len(self.raw_payload) > ALPACA_OPTION_CONTRACT_MAX_RAW_BYTES
        ):
            raise AlpacaOptionContractError

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


class OptionSecurityMasterContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: InstrumentId
    provider_alias: InstrumentAlias
    underlying_instrument_id: str
    underlying_symbol: str
    root_symbol: str
    expiration_date: dt.date
    strike_price: Decimal
    contract_type: OptionContractType
    exercise_style: OptionExerciseStyle
    multiplier: Decimal
    tradable: bool
    open_interest: int | None
    open_interest_date: dt.date | None
    close_price: Decimal | None
    close_price_date: dt.date | None
    observed_at: dt.datetime


class OptionContractCatalogRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: OptionContractCatalogRequest
    started_at: dt.datetime
    completed_at: dt.datetime
    status: OptionCatalogStatus
    failure_code: OptionCatalogFailure | None
    receipt_ids: tuple[str, ...] = Field(max_length=8)
    contracts: tuple[OptionSecurityMasterContract, ...] = Field(
        max_length=80_000
    )

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        match self.status:
            case OptionCatalogStatus.SUCCESS:
                variant_valid = (
                    self.failure_code is None
                    and bool(self.receipt_ids)
                    and bool(self.contracts)
                )
            case OptionCatalogStatus.FAILED:
                variant_valid = self.failure_code is not None
            case unreachable:
                assert_never(unreachable)
        identities = tuple(item.instrument.value for item in self.contracts)
        if (
            not variant_valid
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or len(self.receipt_ids) > self.request.max_pages
            or any(_SHA256.fullmatch(value) is None for value in self.receipt_ids)
            or len(self.receipt_ids) != len(set(self.receipt_ids))
            or identities != tuple(sorted(set(identities)))
            or any(
                item.underlying_symbol != self.request.underlying_symbol
                or item.expiration_date != self.request.expiration_date
                or item.contract_type is not self.request.contract_type
                or item.observed_at != self.completed_at
                for item in self.contracts
            )
        ):
            raise AlpacaOptionContractError
        return self

    @property
    def run_id(self) -> str:
        return hashlib.sha256(
            f"alpaca-option-contract|{self.request.request_id}".encode()
        ).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _token(value: str | None) -> bool:
    return value is None or (
        0 < len(value) <= 2_048
        and not any(character < " " for character in value)
    )


__all__ = (
    "ALPACA_OPTION_CONTRACT_MAX_RAW_BYTES",
    "AlpacaOptionContractError",
    "OptionCatalogFailure",
    "OptionCatalogStatus",
    "OptionContractCatalogRequest",
    "OptionContractCatalogRun",
    "OptionContractRawResponse",
    "OptionSecurityMasterContract",
)
