from __future__ import annotations

import httpx2

from scr_backtest.kis_http import (
    begin_retry_capture,
    captured_retry_events,
    end_retry_capture,
    get_with_server_retry,
)


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
        capture = begin_retry_capture()
        try:
            response = get_with_server_retry(
                client,
                "/read-only",
                params={"EXCD": "NAS", "SYMB": "DEMO"},
                headers={"authorization": "Bearer redacted"},
                sleeper=lambda _: None,
            )
            events = captured_retry_events()
        finally:
            end_retry_capture(capture)

    assert response.status_code == 200
    assert attempts == 2
    assert len(events) == 1
    assert events[0].endpoint == "/read-only"
    assert events[0].exchange == "NAS"
    assert events[0].symbol == "DEMO"
    assert events[0].first_status == 500
    assert events[0].final_status == 200
    assert events[0].outcome == "recovered"


def test_get_never_follows_redirects_with_auth_headers() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.host == "openapi.koreainvestment.com":
            return httpx2.Response(
                302,
                headers={"location": "https://example.invalid/collect"},
            )
        return httpx2.Response(200)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
        follow_redirects=True,
    ) as client:
        response = get_with_server_retry(
            client,
            "/read-only",
            params={},
            headers={"authorization": "Bearer redacted"},
            sleeper=lambda _: None,
        )

    assert response.status_code == 302
    assert len(requests) == 1
    assert requests[0].url.host == "openapi.koreainvestment.com"


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


def test_retry_is_recovered_only_when_the_final_response_is_successful() -> None:
    statuses = iter((500, 429))

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(next(statuses)),
        ),
    ) as client:
        capture = begin_retry_capture()
        try:
            response = get_with_server_retry(
                client,
                "/read-only",
                params={},
                headers={},
                sleeper=lambda _: None,
            )
            events = captured_retry_events()
        finally:
            end_retry_capture(capture)

    assert response.status_code == 429
    assert events[0].outcome == "failed"
