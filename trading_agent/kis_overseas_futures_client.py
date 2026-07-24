from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Final

import httpx2

from scr_backtest.kis_http import get_with_server_retry
from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_auth import quote_headers
from trading_agent.kis_overseas_futures_models import (
    KIS_FUTURES_MAX_RAW_BYTES,
    KisFuturesQuoteRawResponse,
    KisFuturesQuoteRequest,
)

KIS_OVERSEAS_FUTURES_BASE_URL: Final = (
    "https://openapi.koreainvestment.com:9443"
)
_PATH: Final = (
    "/uapi/overseas-futureoption/v1/quotations/inquire-price"
)
_TR_ID: Final = "HHDFC55010000"


class KisOverseasFuturesTransportError(RuntimeError):
    pass


class KisOverseasFuturesClient:
    __slots__ = ("_access_token", "_client", "_clock", "_credentials")

    def __init__(
        self,
        client: httpx2.Client,
        credentials: KisCredentials,
        access_token: str,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if (
            str(client.base_url).rstrip("/")
            != KIS_OVERSEAS_FUTURES_BASE_URL
            or client.follow_redirects
            or not access_token
        ):
            raise ValueError("invalid KIS overseas futures client")
        self._client = client
        self._credentials = credentials
        self._access_token = access_token
        self._clock = _clock

    def fetch(
        self,
        request: KisFuturesQuoteRequest,
        symbol: str,
    ) -> KisFuturesQuoteRawResponse:
        if symbol not in request.symbols:
            raise KisOverseasFuturesTransportError
        try:
            response = get_with_server_retry(
                self._client,
                _PATH,
                {"SRS_CD": symbol},
                quote_headers(
                    self._credentials,
                    self._access_token,
                    _TR_ID,
                ),
            )
            if (
                response.history
                or response.url.scheme != "https"
                or response.url.host != "openapi.koreainvestment.com"
                or response.url.port != 9443
                or response.url.path != _PATH
                or 300 <= response.status_code < 400
                or not response.content
                or len(response.content) > KIS_FUTURES_MAX_RAW_BYTES
            ):
                raise KisOverseasFuturesTransportError
            return KisFuturesQuoteRawResponse(
                request_id=request.request_id,
                symbol=symbol,
                received_at=self._clock(),
                status_code=response.status_code,
                content_type=_content_type(response),
                raw_payload=response.content,
            )
        except (
            httpx2.HTTPError,
            TypeError,
            ValueError,
        ):
            raise KisOverseasFuturesTransportError from None


def _content_type(response: httpx2.Response) -> str:
    return (
        response.headers.get("content-type", "application/octet-stream")
        .partition(";")[0]
        .strip()
        .lower()
    )


__all__ = (
    "KIS_OVERSEAS_FUTURES_BASE_URL",
    "KisOverseasFuturesClient",
    "KisOverseasFuturesTransportError",
)
