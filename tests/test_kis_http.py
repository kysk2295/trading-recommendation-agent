from __future__ import annotations

import httpx2

from scr_backtest.kis_http import get_with_server_retry


def test_get_retries_one_transient_server_error() -> None:
    attempts = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(500 if attempts == 1 else 200, json={"ok": True})

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        response = get_with_server_retry(
            client,
            "/read-only",
            params={"symbol": "DEMO"},
            headers={"authorization": "Bearer redacted"},
            sleeper=lambda _: None,
        )

    assert response.status_code == 200
    assert attempts == 2


def test_get_stops_after_one_retry_and_does_not_retry_rate_limits() -> None:
    server_attempts = 0

    def server_error(_: httpx2.Request) -> httpx2.Response:
        nonlocal server_attempts
        server_attempts += 1
        return httpx2.Response(500)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(server_error),
    ) as client:
        repeated = get_with_server_retry(
            client,
            "/read-only",
            params={},
            headers={},
            sleeper=lambda _: None,
        )

    rate_attempts = 0

    def rate_limit(_: httpx2.Request) -> httpx2.Response:
        nonlocal rate_attempts
        rate_attempts += 1
        return httpx2.Response(429)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(rate_limit),
    ) as client:
        limited = get_with_server_retry(
            client,
            "/read-only",
            params={},
            headers={},
            sleeper=lambda _: None,
        )

    assert repeated.status_code == 500
    assert server_attempts == 2
    assert limited.status_code == 429
    assert rate_attempts == 1
