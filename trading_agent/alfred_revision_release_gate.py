from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.alfred_revision_panel_models import AlfredRevisionPanel
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.fred_vintage_dates_models import FredVintageDatesSnapshot

_SHA = re.compile(r"^[0-9a-f]{64}$")
_SERIES = re.compile(r"^[A-Z][A-Z0-9_.-]{0,63}$")


class AlfredRevisionReleaseGateError(ValueError):
    @override
    def __str__(self) -> str:
        return "ALFRED revision release gate is invalid"


class AlfredRevisionReleaseAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["ready"] = "ready"
    panel_id: str
    calendar_snapshot_id: str
    series_id: str
    vintage_dates: tuple[dt.date, ...] = Field(min_length=2, max_length=100)
    assessed_at: dt.datetime

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if (
            _SHA.fullmatch(self.panel_id) is None
            or _SHA.fullmatch(self.calendar_snapshot_id) is None
            or _SERIES.fullmatch(self.series_id) is None
            or self.vintage_dates != tuple(sorted(set(self.vintage_dates)))
            or not _aware(self.assessed_at)
        ):
            raise AlfredRevisionReleaseGateError
        return self

    @property
    def assessment_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


def build_alfred_revision_release_assessment(
    panel: AlfredRevisionPanel,
    calendar: FredVintageDatesSnapshot,
) -> AlfredRevisionReleaseAssessment:
    try:
        checked_panel = AlfredRevisionPanel.model_validate(
            panel.model_dump(mode="python")
        )
        checked_calendar = FredVintageDatesSnapshot.model_validate(
            calendar.model_dump(mode="python")
        )
        if (
            checked_calendar.series_id != checked_panel.series_id
            or not set(checked_panel.vintage_dates).issubset(
                checked_calendar.vintage_dates
            )
        ):
            raise AlfredRevisionReleaseGateError
        return AlfredRevisionReleaseAssessment(
            panel_id=checked_panel.panel_id,
            calendar_snapshot_id=checked_calendar.snapshot_id,
            series_id=checked_panel.series_id,
            vintage_dates=checked_panel.vintage_dates,
            assessed_at=max(
                checked_panel.latest_source_observed_at,
                checked_calendar.observed_at,
            ),
        )
    except AlfredRevisionReleaseGateError:
        raise
    except (TypeError, ValidationError, ValueError):
        raise AlfredRevisionReleaseGateError from None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlfredRevisionReleaseAssessment",
    "AlfredRevisionReleaseGateError",
    "build_alfred_revision_release_assessment",
)
