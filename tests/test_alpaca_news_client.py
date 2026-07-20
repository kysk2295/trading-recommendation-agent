from __future__ import annotations

import datetime as dt

import httpx2
import pytest

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_news_client import (
    ALPACA_NEWS_MAX_RAW_BYTES,
    AlpacaNewsClient,
    AlpacaNewsTransportError,
)
from trading_agent.alpaca_news_models import AlpacaNewsRequest

START = dt.datetime(2026, 7, 21, 13, tzinfo=dt.UTC)
END = START + dt.timedelta(hours=1)
RECEIVED = END + dt.timedelta(seconds=1)
CREDENTIALS = AlpacaCredentials("test-key", "test-secret")


def _request() -> AlpacaNewsRequest:
    return AlpacaNewsRequest(
        collection_id="news-cycle-001",
        symbols=("TSLA", "AAPL"),
        start_at=START,
        end_at=END,
        limit=50,
        max_pages=8,
    )


def test_client_sends_exact_bounded_news_request_and_preserves_wire_bytes() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            stream=httpx2.ByteStream(b'{"news":[],"next_page_token":null}'),
        )

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        response = AlpacaNewsClient(
            http_client,
            CREDENTIALS,
            _clock=lambda: RECEIVED,
        ).fetch_page(_request(), 0, None)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v1beta1/news"
    assert request.url.params["symbols"] == "AAPL,TSLA"
    assert request.url.params["start"] == START.isoformat()
    assert request.url.params["end"] == END.isoformat()
    assert request.url.params["sort"] == "asc"
    assert request.url.params["limit"] == "50"
    assert request.url.params["include_content"] == "false"
    assert request.headers["accept-encoding"] == "gzip, deflate"
    assert request.headers["apca-api-key-id"] == "test-key"
    assert request.headers["apca-api-secret-key"] == "test-secret"
    assert response.raw_payload == b'{"news":[],"next_page_token":null}'
    assert "test-secret" not in repr(response)


@pytest.mark.parametrize(
    ("base_url", "follow_redirects"),
    (
        ("https://data.alpaca.markets.evil.example", False),
        ("https://data.alpaca.markets", True),
    ),
)
def test_client_rejects_unsafe_transport_before_request(
    base_url: str,
    follow_redirects: bool,
) -> None:
    called = False

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200, request=request, content=b"{}")

    with (
        httpx2.Client(
            base_url=base_url,
            transport=httpx2.MockTransport(handle),
            follow_redirects=follow_redirects,
        ) as http_client,
        pytest.raises(AlpacaNewsTransportError),
    ):
        _ = AlpacaNewsClient(http_client, CREDENTIALS)

    assert called is False


def test_client_rejects_oversized_page_without_leaking_credentials() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-length": str(ALPACA_NEWS_MAX_RAW_BYTES + 1)},
            content=b"{}",
        )

    with (
        httpx2.Client(
            base_url="https://data.alpaca.markets",
            transport=httpx2.MockTransport(handle),
            follow_redirects=False,
        ) as http_client,
        pytest.raises(AlpacaNewsTransportError) as captured,
    ):
        _ = AlpacaNewsClient(http_client, CREDENTIALS).fetch_page(_request(), 0, None)

    assert "test-key" not in str(captured.value)
    assert "test-secret" not in str(captured.value)
