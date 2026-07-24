from __future__ import annotations

import datetime as dt
import json
import socket
from collections.abc import Callable
from typing import Final

import httpx2

from trading_agent.bls_public_collection import BlsPublicTransportError
from trading_agent.bls_public_models import (
    BLS_PUBLIC_MAX_RAW_BYTES,
    BlsPublicRawResponse,
    BlsPublicRequest,
)

BLS_PUBLIC_BASE_URL: Final = "https://api.bls.gov"
_PATH: Final = "/publicAPI/v1/timeseries/data/"


class BlsPublicClient:
    __slots__ = ("_client", "_clock")

    def __init__(
        self,
        client: httpx2.Client,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != BLS_PUBLIC_BASE_URL or client.follow_redirects:
            raise BlsPublicTransportError
        self._client = client
        self._clock = _clock

    def fetch(self, request: BlsPublicRequest) -> BlsPublicRawResponse:
        body = json.dumps(
            {
                "seriesid": request.series_ids,
                "startyear": str(request.start_year),
                "endyear": str(request.end_year),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        try:
            with self._client.stream(
                "POST",
                _PATH,
                content=body,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Content-Type": "application/json",
                },
            ) as response:
                if (
                    response.history
                    or response.url.scheme != "https"
                    or response.url.host != "api.bls.gov"
                    or response.url.path != _PATH
                    or 300 <= response.status_code < 400
                ):
                    raise BlsPublicTransportError
                declared = response.headers.get("content-length")
                if declared is not None and (
                    not declared.isdigit() or int(declared) > BLS_PUBLIC_MAX_RAW_BYTES
                ):
                    raise BlsPublicTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if len(payload) + len(chunk) > BLS_PUBLIC_MAX_RAW_BYTES:
                        raise BlsPublicTransportError
                    payload.extend(chunk)
                return BlsPublicRawResponse(
                    request_id=request.request_id,
                    received_at=self._clock(),
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    raw_payload=bytes(payload),
                )
        except (httpx2.HTTPError, TypeError, ValueError):
            raise BlsPublicTransportError from None


def create_bls_public_http_client() -> httpx2.Client:
    limits = httpx2.Limits(
        max_connections=200,
        max_keepalive_connections=40,
        keepalive_expiry=30.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=limits,
        socket_options=[
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
        ],
    )
    return httpx2.Client(
        base_url=BLS_PUBLIC_BASE_URL,
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
        response.headers.get("content-type", "application/octet-stream")
        .partition(";")[0]
        .strip()
        .lower()
    )


__all__ = (
    "BLS_PUBLIC_BASE_URL",
    "BlsPublicClient",
    "create_bls_public_http_client",
)
