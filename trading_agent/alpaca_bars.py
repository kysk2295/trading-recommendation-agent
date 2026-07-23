from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_http import AlpacaApiError, AlpacaCredentials
from trading_agent.alpaca_models import (
    BARS_ADAPTER,
    ERROR_ADAPTER,
    AlpacaBarsPayload,
    AlpacaBarWindow,
)

NEW_YORK: Final = ZoneInfo("America/New_York")


class AlpacaDailyFeed(StrEnum):
    IEX = "iex"
    SIP = "sip"


@dataclass(frozen=True, slots=True)
class AlpacaPageRequest:
    session_date: dt.date
    symbols: tuple[str, ...]
    window: AlpacaBarWindow
    page_token: str | None = None


@dataclass(frozen=True, slots=True)
class AlpacaDailyPageRequest:
    session_date: dt.date
    symbols: tuple[str, ...]
    start_date: dt.date
    end_date: dt.date
    page_token: str | None = None
    feed: AlpacaDailyFeed = AlpacaDailyFeed.SIP


class AlpacaBarsClient:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaCredentials,
        request_interval_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._request_interval_seconds = request_interval_seconds
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_request_started_at: float | None = None

    def fetch_page(self, request: AlpacaPageRequest) -> AlpacaBarsPayload:
        session_start = dt.datetime.combine(
            request.session_date,
            request.window.start,
            tzinfo=NEW_YORK,
        )
        session_end = dt.datetime.combine(
            request.session_date,
            request.window.end,
            tzinfo=NEW_YORK,
        )
        params = {
            "symbols": ",".join(request.symbols),
            "timeframe": "1Min",
            "start": session_start.astimezone(dt.UTC).isoformat(),
            "end": session_end.astimezone(dt.UTC).isoformat(),
            "limit": "10000",
            "adjustment": "raw",
            "feed": "sip",
            "asof": request.session_date.isoformat(),
            "sort": "asc",
        }
        if request.page_token is not None:
            params["page_token"] = request.page_token
        return self._fetch_payload(params)

    def fetch_daily_page(self, request: AlpacaDailyPageRequest) -> AlpacaBarsPayload:
        params = {
            "symbols": ",".join(request.symbols),
            "timeframe": "1Day",
            "start": request.start_date.isoformat(),
            "end": request.end_date.isoformat(),
            "limit": "10000",
            "adjustment": "raw",
            "feed": request.feed.value,
            "asof": request.session_date.isoformat(),
            "sort": "asc",
        }
        if request.page_token is not None:
            params["page_token"] = request.page_token
        return self._fetch_payload(params)

    def _fetch_payload(self, params: dict[str, str]) -> AlpacaBarsPayload:
        self._wait_for_request_slot()
        response = self._client.get(
            "/v2/stocks/bars",
            params=params,
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
            raise AlpacaApiError(status_code=response.status_code, message=message)
        return BARS_ADAPTER.validate_json(response.content)

    def _wait_for_request_slot(self) -> None:
        now = self._monotonic()
        if self._last_request_started_at is not None:
            delay = self._request_interval_seconds - (now - self._last_request_started_at)
            if delay > 0:
                self._sleeper(delay)
                now = self._monotonic()
        self._last_request_started_at = now
