from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

_SHA = re.compile(r"^[0-9a-f]{64}$")
_SERIES = re.compile(r"^[A-Z][A-Z0-9_.-]{0,63}$")


class AlfredRevisionPanelError(ValueError):
    @override
    def __str__(self) -> str:
        return "ALFRED revision panel is invalid"


class AlfredRevisionState(StrEnum):
    NOT_OBSERVED = "not_observed"
    MISSING = "missing"
    AVAILABLE = "available"


class AlfredRevisionCell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    vintage_date: dt.date
    snapshot_id: str
    state: AlfredRevisionState
    value: Decimal | None
    revision_from_previous_available: Decimal | None

    @model_validator(mode="after")
    def validate_cell(self) -> Self:
        available = self.state is AlfredRevisionState.AVAILABLE
        if (
            _SHA.fullmatch(self.snapshot_id) is None
            or available != (self.value is not None)
            or (not available and self.revision_from_previous_available is not None)
            or (self.value is not None and not self.value.is_finite())
            or (
                self.revision_from_previous_available is not None
                and not self.revision_from_previous_available.is_finite()
            )
        ):
            raise AlfredRevisionPanelError
        return self


class AlfredRevisionRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_date: dt.date
    cells: tuple[AlfredRevisionCell, ...] = Field(min_length=2, max_length=100)


class AlfredRevisionPanel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    series_id: str
    units: str
    observation_start: dt.date
    observation_end: dt.date
    latest_source_observed_at: dt.datetime
    vintage_dates: tuple[dt.date, ...] = Field(min_length=2, max_length=100)
    source_snapshot_ids: tuple[str, ...] = Field(min_length=2, max_length=100)
    rows: tuple[AlfredRevisionRow, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_panel(self) -> Self:
        row_dates = tuple(row.observation_date for row in self.rows)
        if (
            _SERIES.fullmatch(self.series_id) is None
            or not self.units
            or self.units != self.units.strip()
            or self.observation_start > self.observation_end
            or not _aware(self.latest_source_observed_at)
            or self.vintage_dates != tuple(sorted(set(self.vintage_dates)))
            or len(self.vintage_dates) != len(self.source_snapshot_ids)
            or len(set(self.source_snapshot_ids)) != len(self.source_snapshot_ids)
            or any(_SHA.fullmatch(item) is None for item in self.source_snapshot_ids)
            or row_dates != tuple(sorted(set(row_dates)))
            or any(
                date < self.observation_start or date > self.observation_end
                for date in row_dates
            )
        ):
            raise AlfredRevisionPanelError
        for row in self.rows:
            self._validate_row(row)
        return self

    def _validate_row(self, row: AlfredRevisionRow) -> None:
        if tuple(cell.vintage_date for cell in row.cells) != self.vintage_dates:
            raise AlfredRevisionPanelError
        if tuple(cell.snapshot_id for cell in row.cells) != self.source_snapshot_ids:
            raise AlfredRevisionPanelError
        previous: Decimal | None = None
        for cell in row.cells:
            expected = None
            if cell.value is not None:
                if previous is not None:
                    expected = cell.value - previous
                previous = cell.value
            if cell.revision_from_previous_available != expected:
                raise AlfredRevisionPanelError

    @property
    def comparable_revision_count(self) -> int:
        return sum(
            cell.revision_from_previous_available is not None
            for row in self.rows
            for cell in row.cells
        )

    @property
    def changed_revision_count(self) -> int:
        return sum(
            cell.revision_from_previous_available not in (None, Decimal(0))
            for row in self.rows
            for cell in row.cells
        )

    @property
    def panel_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlfredRevisionCell",
    "AlfredRevisionPanel",
    "AlfredRevisionPanelError",
    "AlfredRevisionRow",
    "AlfredRevisionState",
)
