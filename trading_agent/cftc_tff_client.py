from __future__ import annotations

import datetime as dt
import socket
from collections.abc import Callable
from typing import Final

import httpx2

from trading_agent.cftc_tff_collection import CftcTffTransportError
from trading_agent.cftc_tff_models import (
    CFTC_TFF_MAX_RAW_BYTES,
    CftcTffRawResponse,
    CftcTffRequest,
)

CFTC_TFF_BASE_URL: Final = "https://publicreporting.cftc.gov"
_PATH: Final = "/resource/gpe5-46if.json"
_SELECT: Final = ",".join(
    (
        "market_and_exchange_names",
        "report_date_as_yyyy_mm_dd",
        "cftc_contract_market_code",
        "contract_units",
        "open_interest_all",
        "dealer_positions_long_all",
        "dealer_positions_short_all",
        "dealer_positions_spread_all",
        "asset_mgr_positions_long",
        "asset_mgr_positions_short",
        "asset_mgr_positions_spread",
        "lev_money_positions_long",
        "lev_money_positions_short",
        "lev_money_positions_spread",
        "other_rept_positions_long",
        "other_rept_positions_short",
        "other_rept_positions_spread",
        "nonrept_positions_long_all",
        "nonrept_positions_short_all",
        "futonly_or_combined",
    )
)


class CftcTffClient:
    __slots__ = ("_client", "_clock")

    def __init__(
        self,
        client: httpx2.Client,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != CFTC_TFF_BASE_URL or client.follow_redirects:
            raise CftcTffTransportError
        self._client = client
        self._clock = _clock

    def fetch(
        self,
        request: CftcTffRequest,
    ) -> CftcTffRawResponse:
        through = f"{request.through_date.isoformat()}T00:00:00.000"
        where = (
            f"cftc_contract_market_code='{request.contract_market_code}' "
            "AND futonly_or_combined='FutOnly' "
            f"AND report_date_as_yyyy_mm_dd<='{through}'"
        )
        try:
            with self._client.stream(
                "GET",
                _PATH,
                params={
                    "$select": _SELECT,
                    "$where": where,
                    "$order": "report_date_as_yyyy_mm_dd DESC",
                    "$limit": "2",
                },
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if (
                    response.history
                    or response.url.scheme != "https"
                    or response.url.host != "publicreporting.cftc.gov"
                    or response.url.path != _PATH
                    or 300 <= response.status_code < 400
                ):
                    raise CftcTffTransportError
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isdigit() or int(declared) > CFTC_TFF_MAX_RAW_BYTES):
                    raise CftcTffTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if len(payload) + len(chunk) > CFTC_TFF_MAX_RAW_BYTES:
                        raise CftcTffTransportError
                    payload.extend(chunk)
                return CftcTffRawResponse(
                    request_id=request.request_id,
                    received_at=self._clock(),
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    raw_payload=bytes(payload),
                )
        except (httpx2.HTTPError, TypeError, ValueError):
            raise CftcTffTransportError from None


def create_cftc_tff_http_client() -> httpx2.Client:
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
        base_url=CFTC_TFF_BASE_URL,
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
    "CFTC_TFF_BASE_URL",
    "CftcTffClient",
    "create_cftc_tff_http_client",
)
