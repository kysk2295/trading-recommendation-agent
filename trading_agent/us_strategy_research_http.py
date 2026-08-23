from __future__ import annotations

import datetime as dt

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from trading_agent.alpaca_bars import AlpacaBarsClient, AlpacaPageRequest
from trading_agent.alpaca_http import AlpacaApiError, AlpacaCredentials
from trading_agent.alpaca_models import ERROR_ADAPTER, AlpacaBar, AlpacaBarWindow
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_strategy_research_source import UsLatestQuote, UsStrategyResearchSourceError


class _LatestQuotePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    ask: float = Field(alias="ap", gt=0, allow_inf_nan=False)
    ask_size: int = Field(default=0, alias="as", ge=0)
    bid: float = Field(alias="bp", gt=0, allow_inf_nan=False)
    bid_size: int = Field(default=0, alias="bs", ge=0)
    observed_at: dt.datetime = Field(alias="t")


class _LatestQuotesPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    quotes: dict[str, _LatestQuotePayload]


_LATEST_QUOTES_ADAPTER = TypeAdapter(_LatestQuotesPayload)


class AlpacaUsStrategyResearchClient:
    __slots__ = ("_bars", "_client", "_credentials")

    def __init__(self, client: httpx2.Client, credentials: AlpacaCredentials) -> None:
        self._client = client
        self._credentials = credentials
        self._bars = AlpacaBarsClient(client, credentials, request_interval_seconds=0)

    def fetch(
        self,
        symbols: tuple[str, ...],
        now: dt.datetime,
    ) -> tuple[dict[str, tuple[AlpacaBar, ...]], dict[str, UsLatestQuote]]:
        if not symbols or len(symbols) != len(set(symbols)) or now.tzinfo is None or now.utcoffset() is None:
            raise UsStrategyResearchSourceError("source_request_invalid")
        local = now.astimezone(NEW_YORK)
        bounds = regular_session_bounds(local.date())
        if bounds is None or not bounds[0] < now < bounds[1]:
            raise UsStrategyResearchSourceError("session_closed")
        completed_boundary = local.replace(second=0, microsecond=0)
        if completed_boundary.time() <= bounds[0].time():
            raise UsStrategyResearchSourceError("completed_bar_missing")
        payload = self._bars.fetch_page(
            AlpacaPageRequest(
                session_date=local.date(),
                symbols=symbols,
                window=AlpacaBarWindow(bounds[0].time(), completed_boundary.time()),
            )
        )
        if payload.next_page_token is not None:
            raise UsStrategyResearchSourceError("bar_page_incomplete")
        quotes = self._fetch_latest_quotes(symbols)
        return payload.bars, quotes

    def _fetch_latest_quotes(self, symbols: tuple[str, ...]) -> dict[str, UsLatestQuote]:
        response = self._client.get(
            "/v2/stocks/quotes/latest",
            params={"symbols": ",".join(symbols), "feed": "sip"},
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
        payload = _LATEST_QUOTES_ADAPTER.validate_json(response.content)
        return {
            symbol: UsLatestQuote(
                symbol=symbol,
                bid=quote.bid,
                ask=quote.ask,
                bid_size=quote.bid_size,
                ask_size=quote.ask_size,
                observed_at=quote.observed_at,
            )
            for symbol, quote in payload.quotes.items()
        }


__all__ = ("AlpacaUsStrategyResearchClient",)
