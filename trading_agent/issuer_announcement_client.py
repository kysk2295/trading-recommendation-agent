from __future__ import annotations

import datetime as dt
import re
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import NoReturn, override

import httpx2

from trading_agent.issuer_announcement_models import (
    ISSUER_ANNOUNCEMENT_MAX_RAW_BYTES,
    IssuerAnnouncementOnboarding,
    IssuerAnnouncementRawReceipt,
)

_REQUEST_SECONDS = 45.0
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class IssuerAnnouncementTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "issuer announcement transport failed"


class _DeadlineExpired(TimeoutError):
    pass


def fetch_issuer_announcement_feed(
    onboarding: IssuerAnnouncementOnboarding,
    request_id: str,
) -> IssuerAnnouncementRawReceipt:
    try:
        with (
            httpx2.Client(follow_redirects=False, timeout=_REQUEST_SECONDS) as client,
            _deadline(),
            client.stream(
                "GET",
                onboarding.endpoint,
                headers={
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                    "Accept-Encoding": "identity",
                    "User-Agent": "trading-recommendation-agent/issuer-announcement-research",
                },
            ) as response,
        ):
            if response.history or str(response.url) != onboarding.endpoint:
                raise IssuerAnnouncementTransportError
            declared = response.headers.get("content-length")
            if declared is not None and (
                not declared.isdigit()
                or int(declared) > ISSUER_ANNOUNCEMENT_MAX_RAW_BYTES
            ):
                raise IssuerAnnouncementTransportError
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise IssuerAnnouncementTransportError
            payload = bytearray()
            for chunk in response.iter_raw(chunk_size=None):
                if len(payload) + len(chunk) > ISSUER_ANNOUNCEMENT_MAX_RAW_BYTES:
                    raise IssuerAnnouncementTransportError
                payload.extend(chunk)
            return IssuerAnnouncementRawReceipt.from_raw(
                request_id=request_id,
                received_at=dt.datetime.now(dt.UTC),
                status_code=response.status_code,
                content_type=_content_type(response),
                raw_payload=bytes(payload),
            )
    except (
        httpx2.HTTPError,
        IssuerAnnouncementTransportError,
        _DeadlineExpired,
        TypeError,
        ValueError,
    ):
        raise IssuerAnnouncementTransportError from None


@contextmanager
def _deadline() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        raise IssuerAnnouncementTransportError
    timer = signal.getitimer(signal.ITIMER_REAL)
    if timer[0] > 0 or timer[1] > 0:
        raise IssuerAnnouncementTransportError
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


__all__ = (
    "IssuerAnnouncementTransportError",
    "fetch_issuer_announcement_feed",
)
