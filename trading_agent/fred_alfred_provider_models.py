from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FredProviderObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    realtime_start: dt.date
    realtime_end: dt.date
    date: dt.date
    value: str


class FredProviderResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    realtime_start: dt.date
    realtime_end: dt.date
    observation_start: dt.date
    observation_end: dt.date
    units: str
    output_type: Literal[1]
    file_type: Literal["json"]
    order_by: Literal["observation_date"]
    sort_order: Literal["asc"]
    count: int = Field(ge=0)
    offset: Literal[0]
    limit: int = Field(ge=1, le=100_000)
    observations: tuple[FredProviderObservation, ...]


__all__ = (
    "FredProviderObservation",
    "FredProviderResponse",
)
