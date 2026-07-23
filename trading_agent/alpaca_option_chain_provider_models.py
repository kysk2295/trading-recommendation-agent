from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderOptionChainError(ValueError):
    def __str__(self) -> str:
        return "Alpaca provider option chain payload is invalid"


class OptionQuote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    timestamp: dt.datetime = Field(alias="t")
    ask_exchange: str = Field(alias="ax")
    ask_price: Decimal = Field(alias="ap", ge=0)
    ask_size: Decimal = Field(alias="as", ge=0)
    bid_exchange: str = Field(alias="bx")
    bid_price: Decimal = Field(alias="bp", ge=0)
    bid_size: Decimal = Field(alias="bs", ge=0)
    conditions: tuple[str, ...] | str | None = Field(default=None, alias="c")


class OptionTrade(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    timestamp: dt.datetime = Field(alias="t")
    exchange: str = Field(alias="x")
    price: Decimal = Field(alias="p", ge=0)
    size: Decimal = Field(alias="s", ge=0)
    trade_id: int | None = Field(default=None, alias="i")
    conditions: tuple[str, ...] | str | None = Field(default=None, alias="c")


class OptionBar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    timestamp: dt.datetime = Field(alias="t")
    open: Decimal = Field(alias="o", ge=0)
    high: Decimal = Field(alias="h", ge=0)
    low: Decimal = Field(alias="l", ge=0)
    close: Decimal = Field(alias="c", ge=0)
    volume: Decimal = Field(alias="v", ge=0)
    trade_count: int = Field(alias="n", ge=0)
    volume_weighted_price: Decimal = Field(alias="vw", ge=0)

    @model_validator(mode="after")
    def validate_bar(self) -> Self:
        if (
            self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
            or self.low > min(self.open, self.close)
            or self.high < max(self.open, self.close)
            or self.low > self.high
        ):
            raise ProviderOptionChainError
        return self


class OptionGreeks(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delta: Decimal
    gamma: Decimal
    rho: Decimal
    theta: Decimal
    vega: Decimal


class ProviderOptionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    latest_quote: OptionQuote | None = Field(default=None, alias="latestQuote")
    latest_trade: OptionTrade | None = Field(default=None, alias="latestTrade")
    implied_volatility: Decimal | None = Field(
        default=None,
        alias="impliedVolatility",
        ge=0,
    )
    greeks: OptionGreeks | None = None
    minute_bar: OptionBar | None = Field(default=None, alias="minuteBar")
    daily_bar: OptionBar | None = Field(default=None, alias="dailyBar")
    previous_daily_bar: OptionBar | None = Field(default=None, alias="prevDailyBar")


class ProviderOptionChainPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshots: dict[str, ProviderOptionSnapshot]
    next_page_token: str | None = None

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        token = self.next_page_token
        if token is not None and (
            not 0 < len(token) <= 2_048
            or any(character < " " for character in token)
        ):
            raise ProviderOptionChainError
        return self


__all__ = (
    "OptionBar",
    "OptionGreeks",
    "OptionQuote",
    "OptionTrade",
    "ProviderOptionChainPage",
    "ProviderOptionSnapshot",
)
