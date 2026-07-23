from __future__ import annotations

import datetime as dt
import socket
from collections.abc import Callable
from typing import Final, override

import httpx2

from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_option_chain_models import (
    ALPACA_OPTION_CHAIN_MAX_RAW_BYTES,
    OptionChainRawResponse,
    OptionChainRequest,
)

_PATH_PREFIX: Final = "/v1beta1/options/snapshots/"


class AlpacaOptionChainTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca option chain transport failed"


def create_alpaca_option_chain_http_client() -> httpx2.Client:
    limits = httpx2.Limits(
        max_connections=50,
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.Client(
        base_url=ALPACA_DATA_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
        follow_redirects=False,
    )


class AlpacaOptionChainClient:
    __slots__ = ("_client", "_clock", "_credentials")

    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaCredentials,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if (
            str(client.base_url).rstrip("/") != ALPACA_DATA_URL
            or client.follow_redirects
            or type(credentials) is not AlpacaCredentials
            or not credentials.key_id
            or not credentials.secret_key
        ):
            raise AlpacaOptionChainTransportError
        self._client = client
        self._credentials = credentials
        self._clock = _clock

    def fetch_page(
        self,
        request: OptionChainRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionChainRawResponse:
        if not 0 <= page_index < request.max_pages:
            raise AlpacaOptionChainTransportError
        path = f"{_PATH_PREFIX}{request.underlying_symbol}"
        params = {
            "expiration_date": request.expiration_date.isoformat(),
            "feed": request.feed.value,
            "limit": str(request.limit),
            "type": request.contract_type.value,
        }
        if page_token is not None:
            params["page_token"] = page_token
        try:
            with self._client.stream(
                "GET",
                path,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "APCA-API-KEY-ID": self._credentials.key_id,
                    "APCA-API-SECRET-KEY": self._credentials.secret_key,
                },
            ) as response:
                if (
                    response.history
                    or response.url.scheme != "https"
                    or response.url.host != "data.alpaca.markets"
                    or response.url.path != path
                ):
                    raise AlpacaOptionChainTransportError
                declared = response.headers.get("content-length")
                if declared is not None and (
                    not declared.isdigit()
                    or int(declared) > ALPACA_OPTION_CHAIN_MAX_RAW_BYTES
                ):
                    raise AlpacaOptionChainTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if (
                        len(payload) + len(chunk)
                        > ALPACA_OPTION_CHAIN_MAX_RAW_BYTES
                    ):
                        raise AlpacaOptionChainTransportError
                    payload.extend(chunk)
                return OptionChainRawResponse(
                    request_id=request.request_id,
                    page_index=page_index,
                    page_token=page_token,
                    received_at=self._clock(),
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    raw_payload=bytes(payload),
                )
        except (
            httpx2.HTTPError,
            TypeError,
            ValueError,
        ):
            raise AlpacaOptionChainTransportError from None


def _content_type(response: httpx2.Response) -> str:
    media_type = (
        response.headers.get("content-type", "application/octet-stream")
        .partition(";")[0]
        .strip()
        .lower()
    )
    return media_type


__all__ = (
    "AlpacaOptionChainClient",
    "AlpacaOptionChainTransportError",
    "create_alpaca_option_chain_http_client",
)
