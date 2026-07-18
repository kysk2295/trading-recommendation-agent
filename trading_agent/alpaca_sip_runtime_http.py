from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Final

import httpx2
from pydantic import ValidationError

from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_models import BARS_ADAPTER
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipMinutePage,
    AlpacaSipMinutePageRequest,
    AlpacaSipRawPage,
    AlpacaSipRuntimeError,
)

_BARS_PATH: Final = "/v2/stocks/bars"
_MAX_PAGES: Final = 16
_SYMBOL: Final = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,15}$")


class AlpacaSipMinutePageClient:
    __slots__ = ("_client", "_clock", "_credentials")

    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaCredentials,
        *,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._clock = clock

    def fetch_page(self, request: AlpacaSipMinutePageRequest) -> AlpacaSipMinutePage:
        try:
            self._validate(request)
            pages: list[AlpacaSipRawPage] = []
            token: str | None = None
            seen_tokens: set[str] = set()
            while len(pages) < _MAX_PAGES:
                response = self._request(request, token)
                payload = BARS_ADAPTER.validate_json(response.content)
                received_at = self._clock()
                if not _aware(received_at) or set(payload.bars) - {request.symbol}:
                    raise AlpacaSipRuntimeError
                pages.append(
                    AlpacaSipRawPage(
                        page_index=len(pages),
                        page_token=token,
                        received_at=received_at,
                        raw_response=response.content,
                        payload=payload,
                    )
                )
                token = payload.next_page_token
                if token is None:
                    return AlpacaSipMinutePage(request=request, pages=tuple(pages))
                if not _valid_token(token) or token in seen_tokens:
                    raise AlpacaSipRuntimeError
                seen_tokens.add(token)
            raise AlpacaSipRuntimeError
        except (AttributeError, TypeError, ValidationError, ValueError, httpx2.HTTPError):
            raise AlpacaSipRuntimeError from None

    def _request(
        self,
        request: AlpacaSipMinutePageRequest,
        page_token: str | None,
    ) -> httpx2.Response:
        params = {
            "adjustment": "raw",
            "asof": request.session_date.isoformat(),
            "currency": "USD",
            "end": request.end_at.astimezone(dt.UTC).isoformat(),
            "feed": "sip",
            "limit": "10000",
            "sort": "asc",
            "start": request.start_at.astimezone(dt.UTC).isoformat(),
            "symbols": request.symbol,
            "timeframe": "1Min",
        }
        if page_token is not None:
            params["page_token"] = page_token
        response = self._client.get(
            _BARS_PATH,
            params=params,
            headers={
                "APCA-API-KEY-ID": self._credentials.key_id,
                "APCA-API-SECRET-KEY": self._credentials.secret_key,
            },
        )
        if (
            response.status_code != 200
            or response.history
            or response.url.scheme != "https"
            or response.url.host != "data.alpaca.markets"
            or response.url.path != _BARS_PATH
        ):
            raise AlpacaSipRuntimeError
        return response

    def _validate(self, request: AlpacaSipMinutePageRequest) -> None:
        base_url = str(self._client.base_url).rstrip("/")
        if (
            type(request) is not AlpacaSipMinutePageRequest
            or base_url != ALPACA_DATA_URL
            or self._client.follow_redirects
            or type(self._credentials) is not AlpacaCredentials
            or not self._credentials.key_id
            or not self._credentials.secret_key
            or type(request.session_date) is not dt.date
            or not _valid_symbol(request.symbol)
            or not _aware(request.start_at)
            or not _aware(request.end_at)
            or request.start_at >= request.end_at
        ):
            raise AlpacaSipRuntimeError


def _aware(value: object) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


def _valid_symbol(value: object) -> bool:
    return type(value) is str and _SYMBOL.fullmatch(value) is not None


def _valid_token(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 1024 and not any(character in value for character in "\r\n\t")


__all__ = ("AlpacaSipMinutePageClient",)
