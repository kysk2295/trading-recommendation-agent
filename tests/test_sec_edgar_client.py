from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx2
import pytest

from trading_agent.sec_edgar_client import (
    MAX_SEC_SUBMISSION_BYTES,
    SecEdgarClient,
    SecEdgarTransportError,
    UnsafeSecEdgarEndpointError,
    UnsafeSecEdgarRedirectPolicyError,
)
from trading_agent.sec_edgar_config import SecUserAgent

FIXTURE = Path(__file__).parent / "fixtures/sec_edgar/submissions.json"
RECEIVED_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
USER_AGENT = "TradingResearchOS research@example.com"


def test_sec_client_sends_exact_get_with_declared_user_agent() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json; charset=utf-8"},
            content=FIXTURE.read_bytes(),
        )

    with httpx2.Client(
        base_url="https://data.sec.gov",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        response = SecEdgarClient(
            http_client,
            SecUserAgent(USER_AGENT),
            _clock=lambda: RECEIVED_AT,
        ).fetch_submissions("sec-cycle-001", "0000320193")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert request.headers["user-agent"] == USER_AGENT
    assert request.headers["accept"] == "application/json"
    assert request.headers["accept-encoding"] == "gzip, deflate"
    assert response.received_at == RECEIVED_AT
    assert response.content_type == "application/json"
    assert FIXTURE.read_text() not in repr(response)


def test_sec_client_rejects_wrong_origin_and_redirects_before_request() -> None:
    called = False

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200, request=request, content=b"{}")

    with httpx2.Client(
        base_url="https://data.sec.gov.evil.example",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as wrong, pytest.raises(UnsafeSecEdgarEndpointError):
        _ = SecEdgarClient(wrong, SecUserAgent(USER_AGENT))
    with httpx2.Client(
        base_url="https://data.sec.gov",
        transport=httpx2.MockTransport(handle),
        follow_redirects=True,
    ) as redirect, pytest.raises(UnsafeSecEdgarRedirectPolicyError):
        _ = SecEdgarClient(redirect, SecUserAgent(USER_AGENT))
    assert called is False


def test_sec_client_bounds_response_before_collection() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-length": str(MAX_SEC_SUBMISSION_BYTES + 1)},
            content=b"{}",
        )

    with httpx2.Client(
        base_url="https://data.sec.gov",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        client = SecEdgarClient(http_client, SecUserAgent(USER_AGENT))
        with pytest.raises(SecEdgarTransportError) as captured:
            _ = client.fetch_submissions("sec-cycle-001", "0000320193")

    assert "data.sec.gov" not in str(captured.value)
    assert USER_AGENT not in str(captured.value)


def test_sec_client_exposes_read_only_surface() -> None:
    with httpx2.Client(base_url="https://data.sec.gov", follow_redirects=False) as http_client:
        client = SecEdgarClient(http_client, SecUserAgent(USER_AGENT))

    assert {name for name in dir(client) if not name.startswith("_")} == {"fetch_submissions"}
