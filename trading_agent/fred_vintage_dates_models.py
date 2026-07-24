from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_alfred_models import (
    FredFailure,
    FredRawReceipt,
    FredRunStatus,
)

_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SERIES = re.compile(r"^[A-Z][A-Z0-9_.-]{0,63}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class FredVintageDatesError(ValueError):
    @override
    def __str__(self) -> str:
        return "FRED vintage dates evidence is invalid"


class FredVintageDatesRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    series_id: str
    realtime_start: dt.date
    realtime_end: dt.date
    limit: int = Field(ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _ID.fullmatch(self.collection_id) is None
            or _SERIES.fullmatch(self.series_id) is None
            or self.realtime_start > self.realtime_end
        ):
            raise FredVintageDatesError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


class FredVintageDatesProviderResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    realtime_start: dt.date
    realtime_end: dt.date
    order_by: Literal["vintage_date"]
    sort_order: Literal["asc"]
    count: int = Field(ge=0)
    offset: Literal[0]
    limit: int = Field(ge=1, le=10_000)
    vintage_dates: tuple[dt.date, ...]


class FredVintageDatesSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    raw_receipt_id: str
    observed_at: dt.datetime
    series_id: str
    realtime_start: dt.date
    realtime_end: dt.date
    vintage_dates: tuple[dt.date, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if (
            _SHA.fullmatch(self.request_id) is None
            or _SHA.fullmatch(self.raw_receipt_id) is None
            or not _aware(self.observed_at)
            or _SERIES.fullmatch(self.series_id) is None
            or self.realtime_start > self.realtime_end
            or self.vintage_dates != tuple(sorted(set(self.vintage_dates)))
            or any(
                date < self.realtime_start or date > self.realtime_end
                for date in self.vintage_dates
            )
        ):
            raise FredVintageDatesError
        return self

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


class FredVintageDatesTerminal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: FredVintageDatesRequest
    completed_at: dt.datetime
    status: FredRunStatus
    failure: FredFailure | None
    receipt_id: str | None
    snapshot: FredVintageDatesSnapshot | None

    @model_validator(mode="after")
    def validate_terminal(self) -> Self:
        success = self.status is FredRunStatus.SUCCESS
        if (
            not _aware(self.completed_at)
            or success
            != (
                self.failure is None
                and self.receipt_id is not None
                and self.snapshot is not None
            )
            or (
                not success
                and (
                    self.failure is None
                    or self.snapshot is not None
                    or (self.failure is FredFailure.TRANSPORT)
                    != (self.receipt_id is None)
                )
            )
            or (
                self.snapshot is not None
                and (
                    self.snapshot.request_id != self.request.request_id
                    or self.snapshot.raw_receipt_id != self.receipt_id
                    or self.snapshot.series_id != self.request.series_id
                    or self.snapshot.realtime_start != self.request.realtime_start
                    or self.snapshot.realtime_end != self.request.realtime_end
                )
            )
        ):
            raise FredVintageDatesError
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "FredRawReceipt",
    "FredVintageDatesError",
    "FredVintageDatesProviderResponse",
    "FredVintageDatesRequest",
    "FredVintageDatesSnapshot",
    "FredVintageDatesTerminal",
)
