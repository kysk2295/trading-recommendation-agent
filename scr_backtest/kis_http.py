from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Final, Literal

import httpx2

RETRIABLE_SERVER_STATUSES: Final = frozenset((500, 502, 503, 504))
SERVER_RETRY_DELAY_SECONDS: Final = 0.08


@dataclass(frozen=True, slots=True)
class KisReadRetryEvent:
    endpoint: str
    exchange: str
    symbol: str
    first_status: int
    final_status: int
    outcome: Literal["recovered", "failed"]


RetryEvents = tuple[KisReadRetryEvent, ...]
_CAPTURED_RETRIES: ContextVar[RetryEvents | None] = ContextVar(
    "kis_captured_retries",
    default=None,
)


def begin_retry_capture() -> Token[RetryEvents | None]:
    return _CAPTURED_RETRIES.set(())


def captured_retry_events() -> RetryEvents:
    return _CAPTURED_RETRIES.get() or ()


def end_retry_capture(token: Token[RetryEvents | None]) -> None:
    _CAPTURED_RETRIES.reset(token)


def get_with_server_retry(
    client: httpx2.Client,
    path: str,
    params: dict[str, str],
    headers: dict[str, str],
    sleeper: Callable[[float], None] = time.sleep,
) -> httpx2.Response:
    response = client.get(
        path,
        params=params,
        headers=headers,
        follow_redirects=False,
    )
    if response.status_code not in RETRIABLE_SERVER_STATUSES:
        return response
    sleeper(SERVER_RETRY_DELAY_SECONDS)
    retried = client.get(
        path,
        params=params,
        headers=headers,
        follow_redirects=False,
    )
    _capture_retry(
        KisReadRetryEvent(
            endpoint=path,
            exchange=params.get("EXCD", ""),
            symbol=params.get("SYMB", ""),
            first_status=response.status_code,
            final_status=retried.status_code,
            outcome="failed" if retried.is_error else "recovered",
        )
    )
    return retried


def _capture_retry(event: KisReadRetryEvent) -> None:
    events = _CAPTURED_RETRIES.get()
    if events is not None:
        _CAPTURED_RETRIES.set((*events, event))
