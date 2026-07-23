from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Literal, Self, assert_never, override
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.security_master_models import (
    AssetClass,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasType,
    InstrumentId,
)

_ROOT_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FuturesSettlementType(StrEnum):
    CASH = "cash"
    PHYSICAL = "physical"


class FuturesRollSecurityMasterError(ValueError):
    @override
    def __str__(self) -> str:
        return "futures roll security master is invalid"


class FuturesRollContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: InstrumentId
    provider_alias: InstrumentAlias
    root_symbol: str
    settlement_type: FuturesSettlementType
    multiplier: Decimal = Field(gt=0)
    listed_at: dt.datetime
    active_from: dt.datetime
    roll_at: dt.datetime
    first_notice_at: dt.datetime | None
    last_trade_at: dt.datetime
    expiration_date: dt.date
    observed_at: dt.datetime

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        common = (
            self.instrument.market_domain is DataMarketDomain.US_DERIVATIVES
            and self.instrument.asset_class is AssetClass.FUTURE
            and self.provider_alias.instrument_id == self.instrument.value
            and self.provider_alias.alias_type is InstrumentAliasType.PROVIDER_SYMBOL
            and _ROOT_SYMBOL.fullmatch(self.root_symbol) is not None
            and self.multiplier.is_finite()
            and _aware(self.listed_at)
            and _aware(self.active_from)
            and _aware(self.roll_at)
            and _aware(self.last_trade_at)
            and _aware(self.observed_at)
            and self.instrument.valid_from == self.listed_at
            and (self.instrument.valid_to is None or self.roll_at <= self.instrument.valid_to)
            and self.provider_alias.effective_from == self.listed_at
            and (self.provider_alias.effective_to is None or self.roll_at <= self.provider_alias.effective_to)
            and self.listed_at <= self.active_from
            and self.listed_at <= self.observed_at
            and self.active_from < self.roll_at < self.last_trade_at
            and self.expiration_date == self.last_trade_at.astimezone(ZoneInfo(self.instrument.timezone)).date()
        )
        match self.settlement_type:
            case FuturesSettlementType.CASH:
                settlement = self.first_notice_at is None
            case FuturesSettlementType.PHYSICAL:
                settlement = (
                    self.first_notice_at is not None
                    and _aware(self.first_notice_at)
                    and self.roll_at < self.first_notice_at <= self.last_trade_at
                )
            case unreachable:
                assert_never(unreachable)
        if not common or not settlement:
            raise FuturesRollSecurityMasterError
        return self


class FuturesRollManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    root_symbol: str
    source_observed_at: dt.datetime
    source_reference: str
    contracts: tuple[FuturesRollContract, ...] = Field(
        min_length=2,
        max_length=32,
    )


class FuturesRollSecurityMaster(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    root_symbol: str
    source_observed_at: dt.datetime
    source_reference: str
    source_manifest_sha256: str
    contracts: tuple[FuturesRollContract, ...] = Field(
        min_length=2,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_master(self) -> Self:
        if (
            _ROOT_SYMBOL.fullmatch(self.root_symbol) is None
            or not _aware(self.source_observed_at)
            or not _source_reference(self.source_reference)
            or _SHA256.fullmatch(self.source_manifest_sha256) is None
            or not _master_contracts_valid(self)
        ):
            raise FuturesRollSecurityMasterError
        return self

    @property
    def master_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


def _master_contracts_valid(master: FuturesRollSecurityMaster) -> bool:
    contracts = master.contracts
    first = contracts[0]
    shared = (
        first.instrument.venue,
        first.instrument.currency,
        first.instrument.timezone,
        first.multiplier,
        first.provider_alias.namespace,
    )
    ordered = tuple(
        sorted(
            contracts,
            key=lambda item: (
                item.last_trade_at,
                item.instrument.value,
            ),
        )
    )
    identities = tuple(item.instrument.value for item in contracts)
    aliases = tuple(item.provider_alias.canonical_key for item in contracts)
    expirations = tuple(item.expiration_date for item in contracts)
    return (
        contracts == ordered
        and len(set(identities)) == len(contracts)
        and len(set(aliases)) == len(contracts)
        and len(set(expirations)) == len(contracts)
        and first.active_from <= master.source_observed_at < first.roll_at
        and all(
            item.root_symbol == master.root_symbol
            and item.observed_at == master.source_observed_at
            and item.listed_at <= master.source_observed_at
            and (
                item.instrument.venue,
                item.instrument.currency,
                item.instrument.timezone,
                item.multiplier,
                item.provider_alias.namespace,
            )
            == shared
            for item in contracts
        )
        and all(current.active_from == previous.roll_at for previous, current in pairwise(contracts))
    )


def _source_reference(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "FuturesRollContract",
    "FuturesRollManifest",
    "FuturesRollSecurityMaster",
    "FuturesRollSecurityMasterError",
    "FuturesSettlementType",
)
