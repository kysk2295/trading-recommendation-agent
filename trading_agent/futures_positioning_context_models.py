from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from typing import Literal, Self, override
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.cftc_tff_models import (
    CftcTffCategoryPosition,
    CftcTffPositioningContext,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.futures_roll_security_master_models import FuturesRollSecurityMaster
from trading_agent.security_master_models import InstrumentAlias, InstrumentId

_CFTC_CODE = re.compile(r"^[0-9A-Z]{6}$")
_ROOT_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VENUE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,31}$")


class FuturesPositioningContextError(ValueError):
    @override
    def __str__(self) -> str:
        return "futures positioning context is invalid"


class FuturesPositioningBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    cftc_contract_market_code: str
    root_symbol: str
    venue: str
    observed_at: dt.datetime
    effective_from: dt.datetime
    effective_to: dt.datetime | None
    source_reference: str

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        parsed = urlsplit(self.source_reference)
        if (
            _CFTC_CODE.fullmatch(self.cftc_contract_market_code) is None
            or _ROOT_SYMBOL.fullmatch(self.root_symbol) is None
            or _VENUE.fullmatch(self.venue) is None
            or not _aware(self.observed_at)
            or not _aware(self.effective_from)
            or (
                self.effective_to is not None
                and (not _aware(self.effective_to) or self.effective_to <= self.effective_from)
            )
            or parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.fragment)
        ):
            raise FuturesPositioningContextError
        return self


@dataclass(frozen=True, slots=True)
class LoadedCftcTffContext:
    value: CftcTffPositioningContext
    artifact_sha256: str

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.artifact_sha256) is None:
            raise FuturesPositioningContextError


@dataclass(frozen=True, slots=True)
class LoadedFuturesRollMaster:
    value: FuturesRollSecurityMaster
    artifact_sha256: str

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.artifact_sha256) is None:
            raise FuturesPositioningContextError


@dataclass(frozen=True, slots=True)
class LoadedFuturesPositioningBinding:
    value: FuturesPositioningBinding
    artifact_sha256: str

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.artifact_sha256) is None:
            raise FuturesPositioningContextError


class FuturesPositioningJoinRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cftc: LoadedCftcTffContext
    futures_master: LoadedFuturesRollMaster
    binding: LoadedFuturesPositioningBinding
    as_of: dt.datetime
    maximum_report_age_days: int = Field(default=14, ge=1, le=31)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if not _aware(self.as_of):
            raise FuturesPositioningContextError
        return self


class FuturesPositioningContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    as_of: dt.datetime
    maximum_report_age_days: int = Field(ge=1, le=31)
    binding_artifact_sha256: str
    cftc_context_id: str
    cftc_artifact_sha256: str
    futures_master_id: str
    futures_master_artifact_sha256: str
    cftc_contract_market_code: str
    root_symbol: str
    active_instrument: InstrumentId
    active_provider_alias: InstrumentAlias
    active_from: dt.datetime
    roll_at: dt.datetime
    latest_report_date: dt.date
    previous_report_date: dt.date
    cftc_observed_at: dt.datetime
    categories: tuple[CftcTffCategoryPosition, ...] = Field(
        min_length=5,
        max_length=5,
    )

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        hashes = (
            self.binding_artifact_sha256,
            self.cftc_context_id,
            self.cftc_artifact_sha256,
            self.futures_master_id,
            self.futures_master_artifact_sha256,
        )
        if (
            not _aware(self.as_of)
            or any(_SHA256.fullmatch(value) is None for value in hashes)
            or _CFTC_CODE.fullmatch(self.cftc_contract_market_code) is None
            or _ROOT_SYMBOL.fullmatch(self.root_symbol) is None
            or not _aware(self.active_from)
            or not _aware(self.roll_at)
            or not _aware(self.cftc_observed_at)
            or self.active_instrument.value != self.active_provider_alias.instrument_id
            or not self.active_from <= self.as_of < self.roll_at
        ):
            raise FuturesPositioningContextError
        return self

    @property
    def context_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode(),
        ).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "FuturesPositioningBinding",
    "FuturesPositioningContext",
    "FuturesPositioningContextError",
    "FuturesPositioningJoinRequest",
    "LoadedCftcTffContext",
    "LoadedFuturesPositioningBinding",
    "LoadedFuturesRollMaster",
)
