from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

import httpx2

RETRIABLE_SERVER_STATUSES: Final = frozenset((500, 502, 503, 504))
SERVER_RETRY_DELAY_SECONDS: Final = 0.08


def get_with_server_retry(
    client: httpx2.Client,
    path: str,
    params: dict[str, str],
    headers: dict[str, str],
    sleeper: Callable[[float], None] = time.sleep,
) -> httpx2.Response:
    response = client.get(path, params=params, headers=headers)
    if response.status_code not in RETRIABLE_SERVER_STATUSES:
        return response
    sleeper(SERVER_RETRY_DELAY_SECONDS)
    return client.get(path, params=params, headers=headers)
