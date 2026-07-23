from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Final

import httpx2

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_option_contract_collection import (
    AlpacaOptionContractTransportError,
)
from trading_agent.alpaca_option_contract_models import (
    ALPACA_OPTION_CONTRACT_MAX_RAW_BYTES,
    OptionContractCatalogRequest,
    OptionContractRawResponse,
)
from trading_agent.alpaca_paper_contract import ALPACA_PAPER_TRADING_URL

_PATH: Final = "/v2/options/contracts"


class AlpacaOptionContractClient:
    __slots__ = ("_client", "_clock", "_credentials")

    def __init__(
        self,
        client: httpx2.Client,
        credentials: AlpacaCredentials,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if (
            str(client.base_url).rstrip("/") != ALPACA_PAPER_TRADING_URL
            or client.follow_redirects
            or type(credentials) is not AlpacaCredentials
            or not credentials.key_id
            or not credentials.secret_key
        ):
            raise AlpacaOptionContractTransportError
        self._client = client
        self._credentials = credentials
        self._clock = _clock

    def fetch_page(
        self,
        request: OptionContractCatalogRequest,
        page_index: int,
        page_token: str | None,
    ) -> OptionContractRawResponse:
        if not 0 <= page_index < request.max_pages:
            raise AlpacaOptionContractTransportError
        params = {
            "expiration_date": request.expiration_date.isoformat(),
            "limit": str(request.limit),
            "status": "active",
            "type": request.contract_type.value,
            "underlying_symbols": request.underlying_symbol,
        }
        if page_token is not None:
            params["page_token"] = page_token
        try:
            with self._client.stream(
                "GET",
                _PATH,
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
                    or response.url.host != "paper-api.alpaca.markets"
                    or response.url.path != _PATH
                ):
                    raise AlpacaOptionContractTransportError
                declared = response.headers.get("content-length")
                if declared is not None and (
                    not declared.isdigit()
                    or int(declared) > ALPACA_OPTION_CONTRACT_MAX_RAW_BYTES
                ):
                    raise AlpacaOptionContractTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if (
                        len(payload) + len(chunk)
                        > ALPACA_OPTION_CONTRACT_MAX_RAW_BYTES
                    ):
                        raise AlpacaOptionContractTransportError
                    payload.extend(chunk)
                return OptionContractRawResponse(
                    request_id=request.request_id,
                    page_index=page_index,
                    page_token=page_token,
                    received_at=self._clock(),
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    raw_payload=bytes(payload),
                )
        except (httpx2.HTTPError, TypeError, ValueError):
            raise AlpacaOptionContractTransportError from None


def _content_type(response: httpx2.Response) -> str:
    return (
        response.headers.get("content-type", "application/octet-stream")
        .partition(";")[0]
        .strip()
        .lower()
    )


__all__ = ("AlpacaOptionContractClient",)
