from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_option_chain_models import (
    OptionContractType,
    OptionFeed,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

_NEW_YORK = ZoneInfo("America/New_York")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OptionTermStructureStatus(StrEnum):
    READY = "ready"


class AlpacaOptionTermStructureError(ValueError):
    @override
    def __str__(self) -> str:
        return "bounded Alpaca option term structure is invalid"


class OptionTermSlice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    surface_id: str
    surface_sha256: str
    expiration_date: dt.date
    contract_type: OptionContractType
    days_to_expiry: int = Field(ge=1, le=3_650)
    surface_observed_at: dt.datetime
    contract_count: int = Field(ge=1, le=80_000)
    open_interest_observation_count: int = Field(ge=1, le=80_000)
    open_interest_date: dt.date
    total_open_interest: int = Field(ge=0)
    implied_volatility_observation_count: int = Field(ge=1, le=8_000)
    median_implied_volatility: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_slice(self) -> Self:
        if (
            _SHA256.fullmatch(self.surface_id) is None
            or _SHA256.fullmatch(self.surface_sha256) is None
            or not _aware(self.surface_observed_at)
            or self.open_interest_observation_count > self.contract_count
            or self.implied_volatility_observation_count > self.contract_count
        ):
            raise AlpacaOptionTermStructureError
        return self


class AlpacaOptionTermStructure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: OptionTermStructureStatus
    feed: OptionFeed
    underlying_symbol: str
    market_date: dt.date
    as_of: dt.datetime
    maximum_observation_skew_seconds: int = Field(ge=0, le=300)
    expiration_count: int = Field(ge=2, le=32)
    surface_count: int = Field(ge=2, le=32)
    slices: tuple[OptionTermSlice, ...] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        dates = tuple(item.expiration_date for item in self.slices)
        keys = tuple(
            (item.expiration_date, item.contract_type.value) for item in self.slices
        )
        observed = tuple(item.surface_observed_at for item in self.slices)
        if (
            self.status is not OptionTermStructureStatus.READY
            or not self.underlying_symbol
            or not _aware(self.as_of)
            or self.market_date != self.as_of.astimezone(_NEW_YORK).date()
            or self.surface_count != len(self.slices)
            or self.expiration_count != len(set(dates))
            or keys != tuple(sorted(set(keys)))
            or len({item.surface_id for item in self.slices}) != len(self.slices)
            or len({item.surface_sha256 for item in self.slices}) != len(self.slices)
            or max(observed) != self.as_of
            or (self.as_of - min(observed)).total_seconds()
            > self.maximum_observation_skew_seconds
            or any(
                item.expiration_date <= self.market_date
                or item.open_interest_date > self.market_date
                or item.days_to_expiry
                != (item.expiration_date - self.market_date).days
                for item in self.slices
            )
        ):
            raise AlpacaOptionTermStructureError
        return self

    @property
    def term_structure_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaOptionTermStructure",
    "AlpacaOptionTermStructureError",
    "OptionTermSlice",
    "OptionTermStructureStatus",
)
