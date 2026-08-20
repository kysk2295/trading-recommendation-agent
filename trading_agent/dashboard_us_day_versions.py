from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from trading_agent.day_agent_task_store import DayAgentTaskReader

_STALE_AFTER = dt.timedelta(minutes=15)


class DayAgentVersionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always", strict=True)

    version_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    deployment_state: Literal["champion", "shadow"]
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
    observed_at: AwareDatetime

    @field_validator("observed_at", mode="after")
    @classmethod
    def normalize_observed_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)


class DayAgentVersionReader(Protocol):
    def versions(self) -> tuple[DayAgentVersionView, ...]: ...


@dataclass(frozen=True, slots=True)
class DayVersionRead:
    records: tuple[DayAgentVersionView, ...]
    blocker_code: str | None


def read_day_versions(
    reader: DayAgentVersionReader | None,
    task_reader: DayAgentTaskReader | None,
    *,
    now: dt.datetime,
) -> DayVersionRead:
    if reader is None:
        return DayVersionRead((), None)
    try:
        raw_records = reader.versions()
        if not isinstance(raw_records, tuple):
            return DayVersionRead((), "day_version_reader_invalid")
        records = tuple(DayAgentVersionView.model_validate(item, strict=True) for item in raw_records)
        if any(item.observed_at > now or now - item.observed_at > _STALE_AFTER for item in records):
            return DayVersionRead((), "day_version_reader_time_invalid")
        if task_reader is None or any(task_reader.task(item.task_id) is None for item in records):
            return DayVersionRead((), "day_version_task_unlinked")
    except Exception:
        return DayVersionRead((), "day_version_reader_invalid")
    return DayVersionRead(
        tuple(sorted(records, key=lambda item: (item.observed_at, item.version_id), reverse=True)), None
    )


__all__ = ("DayAgentVersionReader", "DayAgentVersionView", "DayVersionRead", "read_day_versions")
