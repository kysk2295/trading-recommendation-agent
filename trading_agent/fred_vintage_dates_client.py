from __future__ import annotations

import datetime as dt
from typing import Final

import httpx2

from trading_agent.fred_alfred_client import FredTransportError
from trading_agent.fred_alfred_config import FredCredentials
from trading_agent.fred_alfred_models import FRED_MAX_RAW_BYTES, FredRawReceipt
from trading_agent.fred_vintage_dates_models import FredVintageDatesRequest

_PATH: Final = "/fred/series/vintagedates"


class FredVintageDatesClient:
    __slots__ = ("_client", "_credentials")

    def __init__(
        self,
        client: httpx2.Client,
        credentials: FredCredentials,
    ) -> None:
        self._client = client
        self._credentials = credentials

    def fetch(self, request: FredVintageDatesRequest) -> FredRawReceipt:
        params = {
            "api_key": self._credentials.api_key,
            "file_type": "json",
            "series_id": request.series_id,
            "realtime_start": request.realtime_start.isoformat(),
            "realtime_end": request.realtime_end.isoformat(),
            "limit": str(request.limit),
            "offset": "0",
            "sort_order": "asc",
        }
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


__all__ = ("FredVintageDatesClient",)
