from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_alfred_models import (
    FredAlfredError,
    FredAlfredRequest,
    FredFailure,
    FredRunStatus,
    FredSourceMode,
)

_SHA = re.compile(r"^[0-9a-f]{64}$")


class FredObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    realtime_start: dt.date
    realtime_end: dt.date
    observation_date: dt.date
    value: Decimal | None

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if (
            self.realtime_start > self.realtime_end
            or (self.value is not None and not self.value.is_finite())
        ):
            raise FredAlfredError
        return self


class FredAlfredSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    raw_receipt_id: str
    observed_at: dt.datetime
    source_mode: FredSourceMode
    series_id: str
    observation_start: dt.date
    observation_end: dt.date
    vintage_date: dt.date | None
    units: str
    observations: tuple[FredObservation, ...] = Field(
        min_length=1,
        max_length=10_000,
    )

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        dates = tuple(item.observation_date for item in self.observations)
        if (
            _SHA.fullmatch(self.request_id) is None
            or _SHA.fullmatch(self.raw_receipt_id) is None
            or not _aware(self.observed_at)
            or not self.units
            or self.units != self.units.strip()
            or dates != tuple(sorted(set(dates)))
            or any(
                date < self.observation_start or date > self.observation_end
                for date in dates
            )
        ):
            raise FredAlfredError
        if self.source_mode is FredSourceMode.ALFRED and any(
            item.realtime_start != self.vintage_date
            or item.realtime_end != self.vintage_date
            for item in self.observations
        ):
            raise FredAlfredError
        return self

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def available_observation_count(self) -> int:
        return sum(item.value is not None for item in self.observations)

    @property
    def observed_completeness_bps(self) -> int:
        return self.available_observation_count * 10_000 // self.observation_count

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


class FredAlfredTerminal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: FredAlfredRequest
    completed_at: dt.datetime
    status: FredRunStatus
    failure: FredFailure | None
    receipt_id: str | None
    snapshot: FredAlfredSnapshot | None

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
                    or self.snapshot.source_mode is not self.request.source_mode
                    or self.snapshot.series_id != self.request.series_id
                    or self.snapshot.observation_start
                    != self.request.observation_start
                    or self.snapshot.observation_end != self.request.observation_end
                    or self.snapshot.vintage_date != self.request.vintage_date
                )
            )
        ):
            raise FredAlfredError
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "FredAlfredSnapshot",
    "FredAlfredTerminal",
    "FredObservation",
)
