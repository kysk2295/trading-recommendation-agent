from __future__ import annotations

import datetime as dt
import re
from typing import Self, override

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.alpaca_http import AlpacaApiError, AlpacaCredentials
from trading_agent.alpaca_models import ERROR_ADAPTER
from trading_agent.us_equity_calendar import NEW_YORK

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")


class InvalidAlpacaMostActiveSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca most-active universe를 안전하게 확인하지 못했습니다"


class AlpacaMostActiveStock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    symbol: str
    volume: int = Field(gt=0)
    trade_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_stock(self) -> Self:
        if _SYMBOL.fullmatch(self.symbol) is None:
            raise InvalidAlpacaMostActiveSourceError
        return self


class AlpacaMostActiveUniverse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    last_updated: dt.datetime
    most_actives: tuple[AlpacaMostActiveStock, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_universe(self) -> Self:
        symbols = tuple(item.symbol for item in self.most_actives)
        volumes = tuple(item.volume for item in self.most_actives)
        if (
            self.last_updated.tzinfo is None
            or self.last_updated.utcoffset() is None
            or len(symbols) != len(set(symbols))
            or volumes != tuple(sorted(volumes, reverse=True))
        ):
            raise InvalidAlpacaMostActiveSourceError
        return self

    @property
    def scanner_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(item.symbol for item in self.most_actives))


class AlpacaMostActiveClient:
    __slots__ = ("_client", "_credentials")

    def __init__(self, client: httpx2.Client, credentials: AlpacaCredentials) -> None:
        self._client = client
        self._credentials = credentials

    def fetch(
        self,
        *,
        top: int,
        session_date: dt.date,
        observed_at: dt.datetime,
    ) -> AlpacaMostActiveUniverse:
        if (
            type(top) is not int
            or not 1 <= top <= 50
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
            or observed_at.astimezone(NEW_YORK).date() != session_date
        ):
            raise InvalidAlpacaMostActiveSourceError
        response = self._client.get(
            "/v1beta1/screener/stocks/most-actives",
            params={"by": "volume", "top": str(top)},
            headers={
                "APCA-API-KEY-ID": self._credentials.key_id,
                "APCA-API-SECRET-KEY": self._credentials.secret_key,
            },
        )
        if response.status_code >= 400:
            try:
                message = ERROR_ADAPTER.validate_json(response.content).message
            except ValidationError:
                message = response.reason_phrase
            raise AlpacaApiError(response.status_code, message)
        try:
            universe = AlpacaMostActiveUniverse.model_validate_json(response.content)
        except ValidationError:
            raise InvalidAlpacaMostActiveSourceError from None
        if (
            len(universe.most_actives) > top
            or universe.last_updated > observed_at
            or universe.last_updated.astimezone(NEW_YORK).date() != session_date
        ):
            raise InvalidAlpacaMostActiveSourceError
        return universe


__all__ = (
    "AlpacaMostActiveClient",
    "AlpacaMostActiveStock",
    "AlpacaMostActiveUniverse",
    "InvalidAlpacaMostActiveSourceError",
)
