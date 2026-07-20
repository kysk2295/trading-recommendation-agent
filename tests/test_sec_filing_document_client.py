from __future__ import annotations

import datetime as dt

import httpx2
import pytest

from trading_agent.sec_edgar_config import SecUserAgent
from trading_agent.sec_filing_document_client import (
    MAX_SEC_FILING_DOCUMENT_BYTES,
    SecFilingDocumentClient,
    SecFilingDocumentTransportError,
    UnsafeSecFilingDocumentEndpointError,
    UnsafeSecFilingDocumentRedirectPolicyError,
)
from trading_agent.sec_filing_document_target import SecFilingDocumentTarget

RECEIVED_AT = dt.datetime(2026, 7, 20, 14, tzinfo=dt.UTC)
USER_AGENT = "TradingResearchOS research@example.com"


def _target() -> SecFilingDocumentTarget:
    return SecFilingDocumentTarget(
        source_version_id="1" * 64,
        source_receipt_id="2" * 64,
        cik="0000320193",
        accession_number="0000000001-26-000101",
        primary_document="exm-20260719.htm",
        accepted_at=dt.datetime(2026, 7, 20, 13, 31, 2, tzinfo=dt.UTC),
        observed_at=RECEIVED_AT,
    )


def test_client_fetches_only_exact_target_path_and_preserves_raw_bytes() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=utf-8"},
            stream=httpx2.ByteStream(b"<html>filing</html>"),
        )

    with httpx2.Client(
        base_url="https://www.sec.gov",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        response = SecFilingDocumentClient(
            http_client,
            SecUserAgent(USER_AGENT),
            _clock=lambda: RECEIVED_AT,
        ).fetch(_target())

    assert tuple(str(item.url) for item in requests) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000000000126000101/exm-20260719.htm",
    )
    assert requests[0].headers["user-agent"] == USER_AGENT
    assert requests[0].headers["accept-encoding"] == "gzip, deflate"
    assert response.raw_payload == b"<html>filing</html>"
    assert response.target_id == _target().target_id
    assert "filing" not in repr(response)


def test_client_rejects_wrong_origin_and_redirect_policy_before_request() -> None:
    called = False

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200, request=request, content=b"ok")

    with (
        httpx2.Client(
            base_url="https://www.sec.gov.evil.example",
            transport=httpx2.MockTransport(handle),
            follow_redirects=False,
        ) as wrong,
        pytest.raises(UnsafeSecFilingDocumentEndpointError),
    ):
        _ = SecFilingDocumentClient(wrong, SecUserAgent(USER_AGENT))
    with (
        httpx2.Client(
            base_url="https://www.sec.gov",
            transport=httpx2.MockTransport(handle),
            follow_redirects=True,
        ) as redirect,
        pytest.raises(UnsafeSecFilingDocumentRedirectPolicyError),
    ):
        _ = SecFilingDocumentClient(redirect, SecUserAgent(USER_AGENT))

    assert called is False


def test_client_rejects_oversized_document_without_leaking_target() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-length": str(MAX_SEC_FILING_DOCUMENT_BYTES + 1)},
            content=b"too large",
        )

    with (
        httpx2.Client(
            base_url="https://www.sec.gov",
            transport=httpx2.MockTransport(handle),
            follow_redirects=False,
        ) as http_client,
        pytest.raises(SecFilingDocumentTransportError) as captured,
    ):
        _ = SecFilingDocumentClient(
            http_client,
            SecUserAgent(USER_AGENT),
        ).fetch(_target())

    assert "sec.gov" not in str(captured.value)
    assert "exm-20260719.htm" not in str(captured.value)


def test_client_normalizes_invalid_content_length_to_document_transport_error() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"content-length": "invalid"},
            content=b"body",
        )

    with (
        httpx2.Client(
            base_url="https://www.sec.gov",
            transport=httpx2.MockTransport(handle),
            follow_redirects=False,
        ) as http_client,
        pytest.raises(SecFilingDocumentTransportError),
    ):
        _ = SecFilingDocumentClient(
            http_client,
            SecUserAgent(USER_AGENT),
        ).fetch(_target())
