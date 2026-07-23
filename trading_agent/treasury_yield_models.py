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

TREASURY_YIELD_MAX_RAW_BYTES: Final = 1024 * 1024
_COLLECTION_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TreasuryMaturity(StrEnum):
    ONE_MONTH = "1_month"
    ONE_AND_HALF_MONTH = "1_5_month"
    TWO_MONTH = "2_month"
    THREE_MONTH = "3_month"
    FOUR_MONTH = "4_month"
    SIX_MONTH = "6_month"
    ONE_YEAR = "1_year"
    TWO_YEAR = "2_year"
    THREE_YEAR = "3_year"
    FIVE_YEAR = "5_year"
    SEVEN_YEAR = "7_year"
    TEN_YEAR = "10_year"
    TWENTY_YEAR = "20_year"
    THIRTY_YEAR = "30_year"


class TreasuryYieldError(ValueError):
    @override
    def __str__(self) -> str:
        return "Treasury yield curve context is invalid"


class TreasuryYieldStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class TreasuryYieldFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"


class TreasuryYieldRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    through_date: dt.date

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if _COLLECTION_ID.fullmatch(self.collection_id) is None:
            raise TreasuryYieldError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode(),
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class TreasuryYieldRawResponse:
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
            or self.content_type not in {"application/xml", "text/xml"}
            or type(self.raw_payload) is not bytes
            or len(self.raw_payload) > TREASURY_YIELD_MAX_RAW_BYTES
        ):
            raise TreasuryYieldError

    @property
    def receipt_id(self) -> str:
        material = "|".join(
            (
                self.request_id,
                self.received_at.astimezone(dt.UTC).isoformat(),
                str(self.status_code),
                self.content_type,
                hashlib.sha256(self.raw_payload).hexdigest(),
            ),
        )
        return hashlib.sha256(material.encode()).hexdigest()


class TreasuryYieldPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    maturity: TreasuryMaturity
    current_percent: Decimal
    previous_percent: Decimal
    change_bps: Decimal

    @model_validator(mode="after")
    def validate_point(self) -> Self:
        values = (self.current_percent, self.previous_percent, self.change_bps)
        if (
            not all(value.is_finite() for value in values)
            or not Decimal("-5") <= self.current_percent <= Decimal("25")
            or not Decimal("-5") <= self.previous_percent <= Decimal("25")
            or self.change_bps != (self.current_percent - self.previous_percent) * 100
        ):
            raise TreasuryYieldError
        return self


class TreasuryYieldContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    raw_receipt_id: str
    latest_date: dt.date
    previous_date: dt.date
    observed_at: dt.datetime
    points: tuple[TreasuryYieldPoint, ...] = Field(
        min_length=14,
        max_length=14,
    )
    ten_year_minus_two_year_bps: Decimal
    ten_year_minus_three_month_bps: Decimal
    thirty_year_minus_five_year_bps: Decimal

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        maturities = tuple(point.maturity for point in self.points)
        current = {point.maturity: point.current_percent for point in self.points}
        slopes = (
            (current[TreasuryMaturity.TEN_YEAR] - current[TreasuryMaturity.TWO_YEAR]) * 100,
            (current[TreasuryMaturity.TEN_YEAR] - current[TreasuryMaturity.THREE_MONTH]) * 100,
            (current[TreasuryMaturity.THIRTY_YEAR] - current[TreasuryMaturity.FIVE_YEAR]) * 100,
        )
        if (
            _SHA256.fullmatch(self.request_id) is None
            or _SHA256.fullmatch(self.raw_receipt_id) is None
            or self.latest_date <= self.previous_date
            or not _aware(self.observed_at)
            or maturities != tuple(TreasuryMaturity)
            or slopes
            != (
                self.ten_year_minus_two_year_bps,
                self.ten_year_minus_three_month_bps,
                self.thirty_year_minus_five_year_bps,
            )
            or not all(value.is_finite() for value in slopes)
        ):
            raise TreasuryYieldError
        return self

    @property
    def context_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode(),
        ).hexdigest()


class TreasuryYieldRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: TreasuryYieldRequest
    started_at: dt.datetime
    completed_at: dt.datetime
    status: TreasuryYieldStatus
    failure: TreasuryYieldFailure | None
    receipt_id: str | None
    context: TreasuryYieldContext | None

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        match self.status:
            case TreasuryYieldStatus.SUCCESS:
                variant_valid = self.failure is None and self.receipt_id is not None and self.context is not None
            case TreasuryYieldStatus.FAILED:
                variant_valid = (
                    self.failure is not None
                    and self.context is None
                    and (
                        (self.failure is TreasuryYieldFailure.TRANSPORT and self.receipt_id is None)
                        or (self.failure is not TreasuryYieldFailure.TRANSPORT and self.receipt_id is not None)
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
            raise TreasuryYieldError
        return self

    @property
    def run_id(self) -> str:
        return hashlib.sha256(
            f"treasury-yield|{self.request.request_id}".encode(),
        ).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "TREASURY_YIELD_MAX_RAW_BYTES",
    "TreasuryMaturity",
    "TreasuryYieldContext",
    "TreasuryYieldError",
    "TreasuryYieldFailure",
    "TreasuryYieldPoint",
    "TreasuryYieldRawResponse",
    "TreasuryYieldRequest",
    "TreasuryYieldRun",
    "TreasuryYieldStatus",
)
