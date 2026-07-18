from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from trading_agent.security_master_models import (
    AssetClass,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasType,
    InstrumentId,
)

_ERROR_MESSAGE: Final = "Alpaca security master is invalid"
_ASSET_ID: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


class AlpacaSecurityMasterError(ValueError):
    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "AlpacaSecurityMasterError()"


class AlpacaSecurityMasterAsset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    asset_id: str = Field(alias="id")
    asset_class: str = Field(alias="class")
    exchange: str
    symbol: str
    name: str
    status: str
    tradable: bool
    marginable: bool | None = None
    maintenance_margin_requirement: Decimal | None = None
    shortable: bool | None = None
    easy_to_borrow: bool | None = None
    fractionable: bool | None = None
    attributes: tuple[str, ...] | None = None
    borrow_status: str | None = None
    margin_requirement_long: Decimal | None = None
    margin_requirement_short: Decimal | None = None

    @model_validator(mode="after")
    def validate_asset(self) -> Self:
        if (
            _ASSET_ID.fullmatch(self.asset_id) is None
            or not _text(self.symbol, 64)
            or not _text(self.exchange, 32)
            or len(self.name) > 512
            or not _text(self.asset_class, 32)
            or not _text(self.status, 32)
        ):
            raise AlpacaSecurityMasterError
        return self


ASSET_RESPONSE_ADAPTER: Final = TypeAdapter(tuple[AlpacaSecurityMasterAsset, ...])


@dataclass(frozen=True, slots=True)
class StoredAlpacaSecurityMasterRaw:
    generation: int
    receipt_id: str
    observed_at: dt.datetime
    payload_sha256: str
    raw_payload: bytes = field(repr=False)


class AlpacaSecurityMasterSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_id: str
    raw_receipt_id: str
    observed_at: dt.datetime
    instruments: tuple[InstrumentId, ...]
    aliases: tuple[InstrumentAlias, ...]

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        instrument_ids = tuple(item.value for item in self.instruments)
        alias_keys = tuple(item.canonical_key for item in self.aliases)
        if (
            _SHA256.fullmatch(self.snapshot_id) is None
            or _SHA256.fullmatch(self.raw_receipt_id) is None
            or not _aware(self.observed_at)
            or not self.instruments
            or instrument_ids != tuple(sorted(set(instrument_ids)))
            or alias_keys != tuple(sorted(set(alias_keys)))
            or len(self.aliases) != len(self.instruments)
            or {item.instrument_id for item in self.aliases} != set(instrument_ids)
            or any(
                item.market_domain is not DataMarketDomain.US_EQUITIES
                or item.asset_class is not AssetClass.EQUITY
                or item.currency != "USD"
                or item.timezone != "America/New_York"
                or item.valid_from != self.observed_at
                or item.valid_to is not None
                for item in self.instruments
            )
            or any(
                item.namespace != "alpaca"
                or item.alias_type is not InstrumentAliasType.PROVIDER_SYMBOL
                or item.effective_from != self.observed_at
                or item.effective_to is not None
                for item in self.aliases
            )
            or self.snapshot_id != _snapshot_id(
                self.raw_receipt_id,
                self.observed_at,
                self.instruments,
                self.aliases,
            )
        ):
            raise AlpacaSecurityMasterError
        return self


def build_alpaca_security_master_snapshot(
    raw_receipt_id: str,
    observed_at: dt.datetime,
    instruments: tuple[InstrumentId, ...],
    aliases: tuple[InstrumentAlias, ...],
) -> AlpacaSecurityMasterSnapshot:
    return AlpacaSecurityMasterSnapshot(
        snapshot_id=_snapshot_id(raw_receipt_id, observed_at, instruments, aliases),
        raw_receipt_id=raw_receipt_id,
        observed_at=observed_at,
        instruments=instruments,
        aliases=aliases,
    )


def _snapshot_id(
    raw_receipt_id: str,
    observed_at: dt.datetime,
    instruments: tuple[InstrumentId, ...],
    aliases: tuple[InstrumentAlias, ...],
) -> str:
    payload = {
        "aliases": [item.model_dump(mode="json") for item in aliases],
        "instruments": [item.model_dump(mode="json") for item in instruments],
        "observed_at": observed_at.isoformat(),
        "raw_receipt_id": raw_receipt_id,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _text(value: str, maximum: int) -> bool:
    return bool(value) and value == value.strip() and len(value) <= maximum


__all__ = (
    "ASSET_RESPONSE_ADAPTER",
    "AlpacaSecurityMasterAsset",
    "AlpacaSecurityMasterError",
    "AlpacaSecurityMasterSnapshot",
    "StoredAlpacaSecurityMasterRaw",
    "build_alpaca_security_master_snapshot",
)
