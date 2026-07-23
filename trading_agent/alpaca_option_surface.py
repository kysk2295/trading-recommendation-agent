from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.alpaca_option_chain_models import (
    OptionChainRun,
    OptionChainStatus,
    OptionContractType,
    OptionFeed,
    OptionGreeks,
    OptionQuote,
    OptionTrade,
)
from trading_agent.alpaca_option_contract_models import (
    OptionCatalogStatus,
    OptionContractCatalogRun,
)
from trading_agent.alpaca_option_contract_provider_models import (
    OptionExerciseStyle,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.security_master_models import InstrumentAliasType

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OptionSurfaceStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"


class AlpacaOptionSurfaceError(ValueError):
    @override
    def __str__(self) -> str:
        return "bounded Alpaca option surface is invalid"


class OptionSurfaceContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str
    provider_symbol: str
    underlying_instrument_id: str
    root_symbol: str
    expiration_date: dt.date
    strike_price: Decimal
    contract_type: OptionContractType
    exercise_style: OptionExerciseStyle
    multiplier: Decimal
    tradable: bool
    open_interest: int | None
    open_interest_date: dt.date | None
    close_price: Decimal | None
    close_price_date: dt.date | None
    master_observed_at: dt.datetime
    snapshot_present: bool
    latest_quote: OptionQuote | None
    latest_trade: OptionTrade | None
    implied_volatility: Decimal | None
    greeks: OptionGreeks | None

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        snapshot_fields = (
            self.latest_quote,
            self.latest_trade,
            self.implied_volatility,
            self.greeks,
        )
        if (
            not self.instrument_id
            or not self.provider_symbol
            or not self.underlying_instrument_id
            or not self.root_symbol
            or not _aware(self.master_observed_at)
            or self.multiplier <= 0
            or (self.open_interest is None)
            != (self.open_interest_date is None)
            or (self.close_price is None) != (self.close_price_date is None)
            or (not self.snapshot_present and any(value is not None for value in snapshot_fields))
            or not _market_observations_valid(self.latest_quote, self.latest_trade)
        ):
            raise AlpacaOptionSurfaceError
        return self


class AlpacaOptionSurface(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: OptionSurfaceStatus
    feed: OptionFeed
    underlying_symbol: str
    expiration_date: dt.date
    contract_type: OptionContractType
    master_request_id: str
    master_run_id: str
    master_run_sha256: str
    chain_request_id: str
    chain_run_id: str
    chain_run_sha256: str
    master_observed_at: dt.datetime
    surface_observed_at: dt.datetime
    master_contract_count: int = Field(ge=1, le=80_000)
    chain_snapshot_count: int = Field(ge=0, le=8_000)
    joined_contract_count: int = Field(ge=0, le=8_000)
    snapshot_coverage_bps: int = Field(ge=0, le=10_000)
    open_interest_count: int = Field(ge=0, le=80_000)
    quote_count: int = Field(ge=0, le=8_000)
    trade_count: int = Field(ge=0, le=8_000)
    implied_volatility_count: int = Field(ge=0, le=8_000)
    greeks_count: int = Field(ge=0, le=8_000)
    contracts: tuple[OptionSurfaceContract, ...] = Field(
        min_length=1,
        max_length=80_000,
    )

    @model_validator(mode="after")
    def validate_surface(self) -> Self:
        instrument_ids = tuple(item.instrument_id for item in self.contracts)
        provider_symbols = tuple(item.provider_symbol for item in self.contracts)
        joined = sum(item.snapshot_present for item in self.contracts)
        expected_status = (
            OptionSurfaceStatus.READY
            if joined == self.master_contract_count
            else OptionSurfaceStatus.DEGRADED
        )
        if (
            any(
                _SHA256.fullmatch(value) is None
                for value in (
                    self.master_request_id,
                    self.master_run_id,
                    self.master_run_sha256,
                    self.chain_request_id,
                    self.chain_run_id,
                    self.chain_run_sha256,
                )
            )
            or not _aware(self.master_observed_at)
            or not _aware(self.surface_observed_at)
            or self.master_observed_at > self.surface_observed_at
            or len(self.contracts) != self.master_contract_count
            or instrument_ids != tuple(sorted(set(instrument_ids)))
            or len(provider_symbols) != len(set(provider_symbols))
            or any(
                item.expiration_date != self.expiration_date
                or item.contract_type is not self.contract_type
                or item.master_observed_at != self.master_observed_at
                for item in self.contracts
            )
            or joined != self.joined_contract_count
            or joined != self.chain_snapshot_count
            or self.snapshot_coverage_bps
            != joined * 10_000 // self.master_contract_count
            or self.open_interest_count
            != sum(item.open_interest is not None for item in self.contracts)
            or self.quote_count
            != sum(item.latest_quote is not None for item in self.contracts)
            or self.trade_count
            != sum(item.latest_trade is not None for item in self.contracts)
            or self.implied_volatility_count
            != sum(item.implied_volatility is not None for item in self.contracts)
            or self.greeks_count
            != sum(item.greeks is not None for item in self.contracts)
            or self.status is not expected_status
        ):
            raise AlpacaOptionSurfaceError
        return self

    @property
    def surface_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


def build_alpaca_option_surface(
    master_run: OptionContractCatalogRun,
    chain_run: OptionChainRun,
) -> AlpacaOptionSurface:
    try:
        master = OptionContractCatalogRun.model_validate(master_run.model_dump())
        chain = OptionChainRun.model_validate(chain_run.model_dump())
        if (
            master.status is not OptionCatalogStatus.SUCCESS
            or chain.status is not OptionChainStatus.SUCCESS
            or master.request.underlying_symbol != chain.request.underlying_symbol
            or master.request.expiration_date != chain.request.expiration_date
            or master.request.contract_type is not chain.request.contract_type
            or master.completed_at > chain.completed_at
        ):
            raise AlpacaOptionSurfaceError

        master_by_symbol = {}
        for contract in master.contracts:
            alias = contract.provider_alias
            if (
                alias.namespace != "alpaca"
                or alias.alias_type is not InstrumentAliasType.PROVIDER_SYMBOL
                or alias.instrument_id != contract.instrument.value
                or alias.effective_from != contract.observed_at
                or (
                    alias.effective_to is not None
                    and alias.effective_to <= chain.completed_at
                )
                or contract.instrument.valid_from != contract.observed_at
                or (
                    contract.instrument.valid_to is not None
                    and contract.instrument.valid_to <= chain.completed_at
                )
                or alias.value in master_by_symbol
            ):
                raise AlpacaOptionSurfaceError
            master_by_symbol[alias.value] = contract

        snapshots = {item.symbol: item for item in chain.snapshots}
        if any(symbol not in master_by_symbol for symbol in snapshots):
            raise AlpacaOptionSurfaceError

        contracts: list[OptionSurfaceContract] = []
        for master_contract in master.contracts:
            symbol = master_contract.provider_alias.value
            snapshot = snapshots.get(symbol)
            if snapshot is not None and (
                snapshot.underlying_symbol != master_contract.underlying_symbol
                or snapshot.expiration_date != master_contract.expiration_date
                or snapshot.contract_type is not master_contract.contract_type
                or snapshot.strike_price != master_contract.strike_price
            ):
                raise AlpacaOptionSurfaceError
            contracts.append(
                OptionSurfaceContract(
                    instrument_id=master_contract.instrument.value,
                    provider_symbol=symbol,
                    underlying_instrument_id=master_contract.underlying_instrument_id,
                    root_symbol=master_contract.root_symbol,
                    expiration_date=master_contract.expiration_date,
                    strike_price=master_contract.strike_price,
                    contract_type=master_contract.contract_type,
                    exercise_style=master_contract.exercise_style,
                    multiplier=master_contract.multiplier,
                    tradable=master_contract.tradable,
                    open_interest=master_contract.open_interest,
                    open_interest_date=master_contract.open_interest_date,
                    close_price=master_contract.close_price,
                    close_price_date=master_contract.close_price_date,
                    master_observed_at=master_contract.observed_at,
                    snapshot_present=snapshot is not None,
                    latest_quote=None if snapshot is None else snapshot.latest_quote,
                    latest_trade=None if snapshot is None else snapshot.latest_trade,
                    implied_volatility=(
                        None if snapshot is None else snapshot.implied_volatility
                    ),
                    greeks=None if snapshot is None else snapshot.greeks,
                )
            )

        joined = len(snapshots)
        master_count = len(contracts)
        return AlpacaOptionSurface(
            status=(
                OptionSurfaceStatus.READY
                if joined == master_count
                else OptionSurfaceStatus.DEGRADED
            ),
            feed=chain.request.feed,
            underlying_symbol=master.request.underlying_symbol,
            expiration_date=master.request.expiration_date,
            contract_type=master.request.contract_type,
            master_request_id=master.request.request_id,
            master_run_id=master.run_id,
            master_run_sha256=_sha256(master),
            chain_request_id=chain.request.request_id,
            chain_run_id=chain.run_id,
            chain_run_sha256=_sha256(chain),
            master_observed_at=master.completed_at,
            surface_observed_at=chain.completed_at,
            master_contract_count=master_count,
            chain_snapshot_count=len(chain.snapshots),
            joined_contract_count=joined,
            snapshot_coverage_bps=joined * 10_000 // master_count,
            open_interest_count=sum(
                item.open_interest is not None for item in contracts
            ),
            quote_count=sum(item.latest_quote is not None for item in contracts),
            trade_count=sum(item.latest_trade is not None for item in contracts),
            implied_volatility_count=sum(
                item.implied_volatility is not None for item in contracts
            ),
            greeks_count=sum(item.greeks is not None for item in contracts),
            contracts=tuple(contracts),
        )
    except AlpacaOptionSurfaceError:
        raise
    except (TypeError, ValidationError, ValueError, ZeroDivisionError):
        raise AlpacaOptionSurfaceError from None


def publish_alpaca_option_surface(
    output_root: Path,
    surface: AlpacaOptionSurface,
) -> tuple[Path, bool]:
    try:
        checked = AlpacaOptionSurface.model_validate(surface.model_dump())
        path = output_root / f"option_surface_{checked.surface_id}.json"
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except AlpacaOptionSurfaceError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise AlpacaOptionSurfaceError from None


def _sha256(value: BaseModel) -> str:
    return hashlib.sha256(
        canonical_experiment_ledger_json(value).encode()
    ).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _market_observations_valid(
    quote: OptionQuote | None,
    trade: OptionTrade | None,
) -> bool:
    return (
        quote is None
        or (
            _aware(quote.timestamp)
            and quote.bid_price <= quote.ask_price
        )
    ) and (trade is None or _aware(trade.timestamp))


__all__ = (
    "AlpacaOptionSurface",
    "AlpacaOptionSurfaceError",
    "OptionSurfaceContract",
    "OptionSurfaceStatus",
    "build_alpaca_option_surface",
    "publish_alpaca_option_surface",
)
