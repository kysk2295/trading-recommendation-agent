from __future__ import annotations

import datetime as dt
import socket
from collections.abc import Callable
from typing import Final

import httpx2

from trading_agent.treasury_yield_collection import (
    TreasuryYieldTransportError,
)
from trading_agent.treasury_yield_models import (
    TREASURY_YIELD_MAX_RAW_BYTES,
    TreasuryYieldRawResponse,
    TreasuryYieldRequest,
)

TREASURY_YIELD_BASE_URL: Final = "https://home.treasury.gov"
_PATH: Final = "/resource-center/data-chart-center/interest-rates/pages/xml"


class TreasuryYieldClient:
    __slots__ = ("_client", "_clock")

    def __init__(
        self,
        client: httpx2.Client,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != TREASURY_YIELD_BASE_URL or client.follow_redirects:
            raise TreasuryYieldTransportError
        self._client = client
        self._clock = _clock

    def fetch(
        self,
        request: TreasuryYieldRequest,
    ) -> TreasuryYieldRawResponse:
        month = request.through_date.strftime("%Y%m")
        try:
            with self._client.stream(
                "GET",
                _PATH,
                params={
                    "data": "daily_treasury_yield_curve",
                    "field_tdr_date_value_month": month,
                },
                headers={
                    "Accept": "application/xml,text/xml",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if (
                    response.history
                    or response.url.scheme != "https"
                    or response.url.host != "home.treasury.gov"
                    or response.url.path != _PATH
                    or 300 <= response.status_code < 400
                ):
                    raise TreasuryYieldTransportError
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isdigit() or int(declared) > TREASURY_YIELD_MAX_RAW_BYTES):
                    raise TreasuryYieldTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if len(payload) + len(chunk) > TREASURY_YIELD_MAX_RAW_BYTES:
                        raise TreasuryYieldTransportError
                    payload.extend(chunk)
                return TreasuryYieldRawResponse(
                    request_id=request.request_id,
                    received_at=self._clock(),
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    raw_payload=bytes(payload),
                )
        except (httpx2.HTTPError, TypeError, ValueError):
            raise TreasuryYieldTransportError from None


def create_treasury_yield_http_client() -> httpx2.Client:
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=httpx2.Limits(
            max_connections=20,
            max_keepalive_connections=5,
            keepalive_expiry=30.0,
        ),
        socket_options=[
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
        ],
    )
    return httpx2.Client(
        base_url=TREASURY_YIELD_BASE_URL,
        transport=transport,
        timeout=httpx2.Timeout(
            connect=5.0,
            read=30.0,
            write=10.0,
            pool=10.0,
        ),
        follow_redirects=False,
    )


def _content_type(response: httpx2.Response) -> str:
    return (
        response.headers.get(
            "content-type",
            "application/octet-stream",
        )
        .partition(";")[0]
        .strip()
        .lower()
    )


__all__ = (
    "TREASURY_YIELD_BASE_URL",
    "TreasuryYieldClient",
    "create_treasury_yield_http_client",
)
