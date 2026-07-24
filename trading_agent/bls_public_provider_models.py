from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.bls_public_models import BlsFootnote


class BlsProviderObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    year: str = Field(pattern=r"^[0-9]{4}$")
    period: str = Field(pattern=r"^[A-Z][0-9]{2}$")
    periodName: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=128)
    latest: Literal["true"] | None = None
    footnotes: tuple[BlsFootnote, ...] = Field(max_length=16)


class BlsProviderSeries(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seriesID: str = Field(pattern=r"^[A-Z0-9_#-]{1,64}$")
    data: tuple[BlsProviderObservation, ...] = Field(min_length=1)


class BlsProviderResults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series: tuple[BlsProviderSeries, ...] = Field(min_length=1, max_length=25)


class BlsProviderResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["REQUEST_SUCCEEDED"]
    responseTime: int = Field(ge=0)
    message: tuple[str, ...] = Field(max_length=32)
    Results: BlsProviderResults


__all__ = (
    "BlsProviderObservation",
    "BlsProviderResponse",
    "BlsProviderResults",
    "BlsProviderSeries",
)
