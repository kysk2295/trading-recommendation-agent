from __future__ import annotations

import datetime as dt
from typing import Final, override

import httpx2

from trading_agent.fred_alfred_config import FredCredentials
from trading_agent.fred_alfred_models import (
    FRED_MAX_RAW_BYTES,
    FredAlfredRequest,
    FredRawReceipt,
    FredSourceMode,
)

_PATH: Final = "/fred/series/observations"


class FredTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "FRED/ALFRED transport failed"


class FredAlfredClient:
    __slots__ = ("_client", "_credentials")

    def __init__(
        self,
        client: httpx2.Client,
        credentials: FredCredentials,
    ) -> None:
        self._client = client
        self._credentials = credentials

    def fetch(self, request: FredAlfredRequest) -> FredRawReceipt:
        params = {
            "api_key": self._credentials.api_key,
            "file_type": "json",
            "series_id": request.series_id,
            "observation_start": request.observation_start.isoformat(),
            "observation_end": request.observation_end.isoformat(),
            "sort_order": "asc",
            "limit": str(request.limit),
            "offset": "0",
            "output_type": "1",
        }
        if request.source_mode is FredSourceMode.ALFRED:
            assert request.vintage_date is not None
            params["vintage_dates"] = request.vintage_date.isoformat()
        try:
            with self._client.stream("GET", _PATH, params=params) as response:
                content_type = response.headers.get("content-type", "").split(";")[0]
                declared = response.headers.get("content-length")
                if declared is not None and (
                    not declared.isdigit()
                    or int(declared) > FRED_MAX_RAW_BYTES
                ):
                    raise FredTransportError
                payload = bytearray()
                for chunk in response.iter_bytes():
                    if len(payload) + len(chunk) > FRED_MAX_RAW_BYTES:
                        raise FredTransportError
                    payload.extend(chunk)
                return FredRawReceipt.from_raw(
                    request_id=request.request_id,
                    received_at=dt.datetime.now(dt.UTC),
                    status_code=response.status_code,
                    content_type=content_type,
                    raw_payload=bytes(payload),
                )
        except FredTransportError:
            raise
        except (httpx2.HTTPError, OSError, TypeError, ValueError):
            raise FredTransportError from None


__all__ = (
    "FredAlfredClient",
    "FredTransportError",
)
