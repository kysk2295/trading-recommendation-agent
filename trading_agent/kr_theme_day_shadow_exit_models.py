from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHADOW_EXIT_SLIPPAGE_BPS = Decimal("20")


class KrThemeDayShadowExitReason(StrEnum):
    STOPPED = "stopped"
    TARGETED = "targeted"
    TIME_EXIT = "time_exit"


class InvalidKrThemeDayShadowExitError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day shadow exit is invalid"


class KrThemeDayShadowExit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    exit_id: str
    entry_id: str
    trial_id: str
    signal_id: str
    strategy_version: str
    symbol: str
    reason: KrThemeDayShadowExitReason
    entry_fill_price: Decimal
    stop_price: Decimal
    first_target_price: Decimal
    trigger_price: Decimal
    exit_slippage_bps: Decimal
    exit_price: Decimal
    exit_at: dt.datetime
    evaluated_at: dt.datetime
    net_return: Decimal
    realized_r: Decimal
    bar_evidence_ids: tuple[str, ...]
    bar_payload_sha256s: tuple[str, ...]

    @model_validator(mode="after")
    def validate_exit(self) -> Self:
        expected_exit = self.trigger_price * (Decimal(1) - SHADOW_EXIT_SLIPPAGE_BPS / Decimal(10_000))
        expected_return = self.exit_price / self.entry_fill_price - Decimal(1)
        expected_r = (self.exit_price - self.entry_fill_price) / (self.entry_fill_price - self.stop_price)
        if (
            not all(_HEX64.fullmatch(value) for value in (self.exit_id, self.entry_id))
            or not self.trial_id
            or not self.signal_id
            or not self.strategy_version
            or not is_kr_instrument_symbol_v2(self.symbol)
            or not all(_positive(value) for value in self._prices())
            or self.stop_price >= self.entry_fill_price
            or self.first_target_price <= self.entry_fill_price
            or self.exit_slippage_bps != SHADOW_EXIT_SLIPPAGE_BPS
            or self.exit_price != expected_exit
            or not _aware(self.exit_at)
            or not _aware(self.evaluated_at)
            or self.exit_at > self.evaluated_at
            or self.net_return != expected_return
            or self.realized_r != expected_r
            or not _ordered_unique(self.bar_evidence_ids)
            or not self.bar_payload_sha256s
            or len(self.bar_payload_sha256s) != len(self.bar_evidence_ids)
            or not all(_HEX64.fullmatch(value) for value in self.bar_payload_sha256s)
        ):
            raise InvalidKrThemeDayShadowExitError
        return self

    def _prices(self) -> tuple[Decimal, ...]:
        return (
            self.entry_fill_price,
            self.stop_price,
            self.first_target_price,
            self.trigger_price,
            self.exit_price,
        )


def _ordered_unique(values: tuple[str, ...]) -> bool:
    return (
        bool(values) and len(values) == len(set(values)) and all(value and value == value.strip() for value in values)
    )


def _positive(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
