from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

CFTC_TFF_MAX_RAW_BYTES: Final = 1024 * 1024
_COLLECTION_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_CONTRACT_MARKET_CODE = re.compile(r"^[0-9A-Z]{6}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class CftcTffCategory(StrEnum):
    DEALER = "dealer"
    ASSET_MANAGER = "asset_manager"
    LEVERAGED_MONEY = "leveraged_money"
    OTHER_REPORTABLE = "other_reportable"
    NONREPORTABLE = "nonreportable"


class CftcTffStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class CftcTffFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"


class CftcTffError(ValueError):
    @override
    def __str__(self) -> str:
        return "CFTC TFF positioning context is invalid"


class CftcTffRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    contract_market_code: str
    through_date: dt.date

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _COLLECTION_ID.fullmatch(self.collection_id) is None
            or _CONTRACT_MARKET_CODE.fullmatch(self.contract_market_code) is None
        ):
            raise CftcTffError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CftcTffRawResponse:
    request_id: str
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            _SHA256.fullmatch(self.request_id) is None
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or type(self.raw_payload) is not bytes
            or len(self.raw_payload) > CFTC_TFF_MAX_RAW_BYTES
        ):
            raise CftcTffError

    @property
    def receipt_id(self) -> str:
        material = "|".join(
            (
                self.request_id,
                self.received_at.astimezone(dt.UTC).isoformat(),
                str(self.status_code),
                self.content_type,
                hashlib.sha256(self.raw_payload).hexdigest(),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class CftcTffProviderRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market_and_exchange_names: str = Field(min_length=1, max_length=256)
    report_date_as_yyyy_mm_dd: dt.datetime
    cftc_contract_market_code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    contract_units: str = Field(min_length=1, max_length=256)
    open_interest_all: int = Field(ge=1)
    dealer_positions_long_all: int = Field(ge=0)
    dealer_positions_short_all: int = Field(ge=0)
    dealer_positions_spread_all: int = Field(ge=0)
    asset_mgr_positions_long: int = Field(ge=0)
    asset_mgr_positions_short: int = Field(ge=0)
    asset_mgr_positions_spread: int = Field(ge=0)
    lev_money_positions_long: int = Field(ge=0)
    lev_money_positions_short: int = Field(ge=0)
    lev_money_positions_spread: int = Field(ge=0)
    other_rept_positions_long: int = Field(ge=0)
    other_rept_positions_short: int = Field(ge=0)
    other_rept_positions_spread: int = Field(ge=0)
    nonrept_positions_long_all: int = Field(ge=0)
    nonrept_positions_short_all: int = Field(ge=0)
    futonly_or_combined: Literal["FutOnly"]

    @model_validator(mode="after")
    def validate_report_date(self) -> Self:
        value = self.report_date_as_yyyy_mm_dd
        if value.tzinfo is not None or value.time() != dt.time():
            raise CftcTffError
        return self

    @property
    def report_date(self) -> dt.date:
        return self.report_date_as_yyyy_mm_dd.date()


class CftcTffCategoryPosition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: CftcTffCategory
    current_net: int
    previous_net: int
    weekly_change: int
    current_net_bps: Decimal

    @model_validator(mode="after")
    def validate_position(self) -> Self:
        if self.weekly_change != self.current_net - self.previous_net or not self.current_net_bps.is_finite():
            raise CftcTffError
        return self


class CftcTffPositioningContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    raw_receipt_id: str
    contract_market_code: str
    market_and_exchange_name: str
    contract_units: str
    latest_report_date: dt.date
    previous_report_date: dt.date
    latest_open_interest: int = Field(gt=0)
    previous_open_interest: int = Field(gt=0)
    observed_at: dt.datetime
    categories: tuple[CftcTffCategoryPosition, ...] = Field(
        min_length=5,
        max_length=5,
    )

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        categories = tuple(item.category for item in self.categories)
        if (
            _SHA256.fullmatch(self.request_id) is None
            or _SHA256.fullmatch(self.raw_receipt_id) is None
            or _CONTRACT_MARKET_CODE.fullmatch(self.contract_market_code) is None
            or not self.market_and_exchange_name
            or not self.contract_units
            or self.latest_report_date <= self.previous_report_date
            or not _aware(self.observed_at)
            or categories != tuple(CftcTffCategory)
        ):
            raise CftcTffError
        return self

    @property
    def context_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


class CftcTffRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: CftcTffRequest
    started_at: dt.datetime
    completed_at: dt.datetime
    status: CftcTffStatus
    failure: CftcTffFailure | None
    receipt_id: str | None
    context: CftcTffPositioningContext | None

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        match self.status:
            case CftcTffStatus.SUCCESS:
                variant_valid = self.failure is None and self.receipt_id is not None and self.context is not None
            case CftcTffStatus.FAILED:
                variant_valid = (
                    self.failure is not None
                    and self.context is None
                    and (
                        (self.failure is CftcTffFailure.TRANSPORT and self.receipt_id is None)
                        or (self.failure is not CftcTffFailure.TRANSPORT and self.receipt_id is not None)
                    )
                )
            case unreachable:
                assert_never(unreachable)
        context_valid = self.context is None or (
            self.context.request_id == self.request.request_id
            and self.context.raw_receipt_id == self.receipt_id
            and self.started_at <= self.context.observed_at <= self.completed_at
        )
        if (
            not variant_valid
            or not context_valid
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or (self.receipt_id is not None and _SHA256.fullmatch(self.receipt_id) is None)
        ):
            raise CftcTffError
        return self

    @property
    def run_id(self) -> str:
        return hashlib.sha256(f"cftc-tff|{self.request.request_id}".encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "CFTC_TFF_MAX_RAW_BYTES",
    "CftcTffCategory",
    "CftcTffCategoryPosition",
    "CftcTffError",
    "CftcTffFailure",
    "CftcTffPositioningContext",
    "CftcTffProviderRow",
    "CftcTffRawResponse",
    "CftcTffRequest",
    "CftcTffRun",
    "CftcTffStatus",
)
