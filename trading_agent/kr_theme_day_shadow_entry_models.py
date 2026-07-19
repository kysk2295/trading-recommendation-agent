from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHADOW_ENTRY_SLIPPAGE_BPS = Decimal("20")


class InvalidKrThemeDayShadowEntryError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day shadow entry is invalid"


class KrThemeDayShadowEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    entry_id: str
    trial_id: str
    trial_registration_key: str
    started_event_key: str
    signal_id: str
    signal_payload_sha256: str
    strategy_version: str
    symbol: str
    signal_observed_at: dt.datetime
    filled_at: dt.datetime
    quoted_entry_price: Decimal
    slippage_bps: Decimal
    fill_price: Decimal
    stop_price: Decimal
    target_prices: tuple[Decimal, ...]
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_entry(self) -> Self:
        hashes = (self.trial_registration_key, self.started_event_key, self.signal_payload_sha256)
        expected_fill = self.quoted_entry_price * (Decimal(1) + SHADOW_ENTRY_SLIPPAGE_BPS / Decimal(10_000))
        if (
            _HEX64.fullmatch(self.entry_id) is None
            or not all(_HEX64.fullmatch(value) for value in hashes)
            or not self.trial_id
            or not self.signal_id
            or not self.strategy_version
            or not is_kr_instrument_symbol_v2(self.symbol)
            or not _aware(self.signal_observed_at)
            or not _aware(self.filled_at)
            or self.filled_at < self.signal_observed_at
            or not _positive(self.quoted_entry_price)
            or self.slippage_bps != SHADOW_ENTRY_SLIPPAGE_BPS
            or self.fill_price != expected_fill
            or not _positive(self.stop_price)
            or self.stop_price >= self.fill_price
            or not self.target_prices
            or any(not _positive(price) or price <= self.fill_price for price in self.target_prices)
            or self.target_prices != tuple(sorted(set(self.target_prices)))
            or not self.evidence_ids
            or self.evidence_ids != tuple(sorted(set(self.evidence_ids)))
        ):
            raise InvalidKrThemeDayShadowEntryError
        return self


def _positive(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
