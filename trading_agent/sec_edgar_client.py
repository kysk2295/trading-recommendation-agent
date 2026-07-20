from __future__ import annotations

import datetime as dt
import re
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Final, NoReturn, final, override

import httpx2

from trading_agent.sec_edgar_config import SEC_EDGAR_BASE_URL, SecUserAgent
from trading_agent.sec_edgar_models import (
    SEC_EDGAR_MAX_RAW_BYTES,
    SecSubmissionRawResponse,
    normalize_sec_cik,
)

MAX_SEC_SUBMISSION_BYTES: Final = SEC_EDGAR_MAX_RAW_BYTES
MAX_SEC_REQUEST_SECONDS: Final = 45.0
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class UnsafeSecEdgarEndpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR client origin must be the official fixed endpoint"


class UnsafeSecEdgarRedirectPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR client must not follow redirects"


class SecEdgarTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "SEC EDGAR transport failed"


class _SecEdgarDeadlineExpired(TimeoutError):
    pass


@final
class SecEdgarClient:
    __slots__ = ("_client", "_clock", "_user_agent")

    def __init__(
        self,
        client: httpx2.Client,
        user_agent: SecUserAgent,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != SEC_EDGAR_BASE_URL:
            raise UnsafeSecEdgarEndpointError
        if client.follow_redirects:
            raise UnsafeSecEdgarRedirectPolicyError
        self._client = client
        self._user_agent = user_agent
        self._clock = _clock

    def fetch_submissions(self, collection_id: str, cik: str) -> SecSubmissionRawResponse:
        if _SAFE_ID.fullmatch(collection_id) is None:
            raise SecEdgarTransportError
        cik = normalize_sec_cik(cik)
        try:
            with _request_deadline(), self._client.stream(
                "GET",
                f"/submissions/CIK{cik}.json",
                headers={
                    "User-Agent": self._user_agent.value,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                },
            ) as response:
                content_length = _content_length(response)
                if content_length is not None and content_length > MAX_SEC_SUBMISSION_BYTES:
                    raise SecEdgarTransportError
                payload = bytearray()
                for chunk in response.iter_raw(chunk_size=None):
                    if len(payload) + len(chunk) > MAX_SEC_SUBMISSION_BYTES:
                        raise SecEdgarTransportError
                    payload.extend(chunk)
                received_at = self._clock()
                status_code = response.status_code
                content_type = _response_content_type(response)
                content_encoding = _response_content_encoding(response)
        except (httpx2.HTTPError, _SecEdgarDeadlineExpired):
            raise SecEdgarTransportError from None
        return SecSubmissionRawResponse(
            collection_id=collection_id,
            cik=cik,
            received_at=received_at,
            status_code=status_code,
            content_type=content_type,
            raw_payload=bytes(payload),
            content_encoding=content_encoding,
        )


@contextmanager
def _request_deadline() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        raise SecEdgarTransportError
    active_timer = signal.getitimer(signal.ITIMER_REAL)
    if active_timer[0] > 0 or active_timer[1] > 0:
        raise SecEdgarTransportError
    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _expire_request)
    _ = signal.setitimer(signal.ITIMER_REAL, MAX_SEC_REQUEST_SECONDS)
    try:
        yield
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, 0)
        _ = signal.signal(signal.SIGALRM, previous_handler)


def _expire_request(_signum: int, _frame: FrameType | None) -> NoReturn:
    raise _SecEdgarDeadlineExpired


def _content_length(response: httpx2.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise SecEdgarTransportError from None
    if parsed < 0:
        raise SecEdgarTransportError
    return parsed


def _response_content_type(response: httpx2.Response) -> str:
    value = response.headers.get("content-type", "application/octet-stream")
    media_type = value.partition(";")[0].strip().lower()
    return media_type if _CONTENT_TYPE.fullmatch(media_type) is not None else "application/octet-stream"


def _response_content_encoding(response: httpx2.Response) -> str:
    value = response.headers.get("content-encoding", "identity").strip().lower()
    return value if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,31}", value) is not None else "unsupported"
