from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class SwingDailyBar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    session_date: dt.date
    observed_at: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @model_validator(mode="after")
    def validate_bar(self) -> Self:
        bounds = regular_session_bounds(self.session_date)
        prices = (self.open, self.high, self.low, self.close)
        if (
            _US_SYMBOL.fullmatch(self.symbol) is None
            or bounds is None
            or not _aware(self.observed_at)
            or self.observed_at.astimezone(NEW_YORK) < bounds[1]
            or not all(value.is_finite() and value > 0 for value in prices)
            or not self.low <= min(self.open, self.close)
            or not max(self.open, self.close) <= self.high
            or self.volume < 0
        ):
            raise ValueError("invalid swing daily bar")
        return self


class SwingDailySource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    session_date: dt.date
    observed_at: dt.datetime
    universe_id: str
    symbols: tuple[str, ...]
    bars: tuple[SwingDailyBar, ...]

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        bounds = regular_session_bounds(self.session_date)
        bar_keys = tuple((bar.symbol, bar.session_date) for bar in self.bars)
        target_symbols = tuple(
            sorted(
                bar.symbol for bar in self.bars if bar.session_date == self.session_date
            )
        )
        if (
            self.schema_version != 1
            or bounds is None
            or not _aware(self.observed_at)
            or self.observed_at.astimezone(NEW_YORK) < bounds[1]
            or _IDENTIFIER.fullmatch(self.universe_id) is None
            or not self.symbols
            or self.symbols != tuple(sorted(set(self.symbols)))
            or not all(_US_SYMBOL.fullmatch(symbol) for symbol in self.symbols)
            or not self.bars
            or bar_keys != tuple(sorted(set(bar_keys)))
            or any(
                bar.symbol not in self.symbols
                or bar.session_date > self.session_date
                or bar.observed_at > self.observed_at
                for bar in self.bars
            )
            or target_symbols != self.symbols
        ):
            raise ValueError("invalid swing daily source")
        return self

    @property
    def source_key(self) -> str:
        return swing_daily_source_key(self)

    def bars_for(self, symbol: str) -> tuple[SwingDailyBar, ...]:
        return tuple(bar for bar in self.bars if bar.symbol == symbol)


def swing_daily_source_key(source: SwingDailySource) -> str:
    payload = source.model_dump(mode="json")
    material = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
