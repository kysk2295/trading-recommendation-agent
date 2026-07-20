from __future__ import annotations

import datetime as dt
import re
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Final, NoReturn, override

import httpx2

from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_news_models import (
    ALPACA_NEWS_MAX_RAW_BYTES as _MAX_RAW_BYTES,
)
from trading_agent.alpaca_news_models import (
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
)

ALPACA_NEWS_MAX_RAW_BYTES: Final = _MAX_RAW_BYTES
_PATH: Final = "/v1beta1/news"
_REQUEST_SECONDS: Final = 45.0
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class AlpacaNewsTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca news transport failed"


class _DeadlineExpired(TimeoutError):
    pass


class AlpacaNewsClient:
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
            raise AlpacaNewsTransportError
        self._client = client
        self._credentials = credentials
        self._clock = _clock

    def fetch_page(
        self,
        request: AlpacaNewsRequest,
        page_index: int,
        page_token: str | None,
    ) -> AlpacaNewsRawResponse:
        if not 0 <= page_index < request.max_pages or not _valid_token(page_token):
            raise AlpacaNewsTransportError
        params = {
            "end": request.end_at.isoformat(),
            "exclude_contentless": "false",
            "include_content": "false",
            "limit": str(request.limit),
            "sort": "asc",
            "start": request.start_at.isoformat(),
            "symbols": ",".join(request.symbols),
        }
        if page_token is not None:
            params["page_token"] = page_token
        try:
            with (
                _deadline(),
                self._client.stream(
                    "GET",
                    _PATH,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip, deflate, br, zstd",
                        "APCA-API-KEY-ID": self._credentials.key_id,
                        "APCA-API-SECRET-KEY": self._credentials.secret_key,
                    },
                ) as response,
            ):
                if (
                    response.history
                    or response.url.scheme != "https"
                    or response.url.host != "data.alpaca.markets"
                    or response.url.path != _PATH
                ):
                    raise AlpacaNewsTransportError
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isdigit() or int(declared) > ALPACA_NEWS_MAX_RAW_BYTES):
                    raise AlpacaNewsTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if len(payload) + len(chunk) > ALPACA_NEWS_MAX_RAW_BYTES:
                        raise AlpacaNewsTransportError
                    payload.extend(chunk)
                return AlpacaNewsRawResponse(
                    request_id=request.request_id,
                    page_index=page_index,
                    page_token=page_token,
                    received_at=self._clock(),
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    content_encoding=_content_encoding(response),
                    raw_payload=bytes(payload),
                )
        except (httpx2.HTTPError, _DeadlineExpired, TypeError, ValueError):
            raise AlpacaNewsTransportError from None


@contextmanager
def _deadline() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        raise AlpacaNewsTransportError
    timer = signal.getitimer(signal.ITIMER_REAL)
    if timer[0] > 0 or timer[1] > 0:
        raise AlpacaNewsTransportError
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _expire)
    _ = signal.setitimer(signal.ITIMER_REAL, _REQUEST_SECONDS)
    try:
        yield
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, 0)
        _ = signal.signal(signal.SIGALRM, previous)


def _expire(_signum: int, _frame: FrameType | None) -> NoReturn:
    raise _DeadlineExpired


def _content_type(response: httpx2.Response) -> str:
    value = response.headers.get("content-type", "application/octet-stream")
    media_type = value.partition(";")[0].strip().lower()
    return media_type if _CONTENT_TYPE.fullmatch(media_type) is not None else "application/octet-stream"


def _content_encoding(response: httpx2.Response) -> str:
    value = response.headers.get("content-encoding", "identity").strip().lower()
    return value if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,31}", value) is not None else "unsupported"


def _valid_token(value: str | None) -> bool:
    return value is None or (0 < len(value) <= 2_048 and not any(character < " " for character in value))
