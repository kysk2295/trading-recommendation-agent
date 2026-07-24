from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal

from pydantic import ValidationError

from trading_agent.alfred_revision_panel_models import (
    AlfredRevisionCell,
    AlfredRevisionPanel,
    AlfredRevisionPanelError,
    AlfredRevisionRow,
    AlfredRevisionState,
)
from trading_agent.fred_alfred_models import FredSourceMode
from trading_agent.fred_alfred_snapshot_models import FredAlfredSnapshot


def build_alfred_revision_panel(
    snapshots: Sequence[FredAlfredSnapshot],
) -> AlfredRevisionPanel:
    try:
        checked = tuple(
            FredAlfredSnapshot.model_validate(item.model_dump(mode="python"))
            for item in snapshots
        )
        if not 2 <= len(checked) <= 100:
            raise AlfredRevisionPanelError
        ordered = tuple(
            sorted(checked, key=lambda item: item.vintage_date or item.observation_end)
        )
        first = ordered[0]
        if (
            any(
                item.source_mode is not FredSourceMode.ALFRED
                or item.vintage_date is None
                or item.series_id != first.series_id
                or item.units != first.units
                or item.observation_start != first.observation_start
                or item.observation_end != first.observation_end
                for item in ordered
            )
            or len({item.vintage_date for item in ordered}) != len(ordered)
            or len({item.snapshot_id for item in ordered}) != len(ordered)
            or any(
                observation.observation_date > item.vintage_date
                for item in ordered
                for observation in item.observations
                if item.vintage_date is not None
            )
        ):
            raise AlfredRevisionPanelError
        dates = tuple(
            sorted(
                {
                    observation.observation_date
                    for item in ordered
                    for observation in item.observations
                }
            )
        )
        rows = tuple(_row(date, ordered) for date in dates)
        return AlfredRevisionPanel(
            series_id=first.series_id,
            units=first.units,
            observation_start=first.observation_start,
            observation_end=first.observation_end,
            latest_source_observed_at=max(item.observed_at for item in ordered),
            vintage_dates=tuple(
                item.vintage_date
                for item in ordered
                if item.vintage_date is not None
            ),
            source_snapshot_ids=tuple(item.snapshot_id for item in ordered),
            rows=rows,
        )
    except AlfredRevisionPanelError:
        raise
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise AlfredRevisionPanelError from None


def _row(
    observation_date: dt.date,
    snapshots: tuple[FredAlfredSnapshot, ...],
) -> AlfredRevisionRow:
    values = (
        next(
            (
                observation.value
                for observation in snapshot.observations
                if observation.observation_date == observation_date
            ),
            _NOT_OBSERVED,
        )
        for snapshot in snapshots
    )
    previous: Decimal | None = None
    cells: list[AlfredRevisionCell] = []
    for snapshot, value in zip(snapshots, values, strict=True):
        vintage_date = snapshot.vintage_date
        if vintage_date is None:
            raise AlfredRevisionPanelError
        revision = None
        if isinstance(value, Decimal):
            if previous is not None:
                revision = value - previous
            previous = value
        cells.append(
            AlfredRevisionCell(
                vintage_date=vintage_date,
                snapshot_id=snapshot.snapshot_id,
                state=(
                    AlfredRevisionState.NOT_OBSERVED
                    if value is _NOT_OBSERVED
                    else (
                        AlfredRevisionState.MISSING
                        if value is None
                        else AlfredRevisionState.AVAILABLE
                    )
                ),
                value=value if isinstance(value, Decimal) else None,
                revision_from_previous_available=revision,
            )
        )
    return AlfredRevisionRow(observation_date=observation_date, cells=tuple(cells))


_NOT_OBSERVED = object()

__all__ = ("AlfredRevisionPanelError", "build_alfred_revision_panel")
