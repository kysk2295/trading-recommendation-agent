from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable

import httpx2
import pytest

from trading_agent.bls_public_client import BlsPublicClient
from trading_agent.bls_public_collection import BlsPublicTransportError
from trading_agent.bls_public_models import (
    BLS_PUBLIC_MAX_RAW_BYTES,
    BlsPublicRequest,
)

RECEIVED = dt.datetime(2026, 7, 24, 1, 2, 3, tzinfo=dt.UTC)


def test_client_uses_fixed_public_v1_post_request() -> None:
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            stream=httpx2.ByteStream(
                b'{"status":"REQUEST_SUCCEEDED","responseTime":1,'
                b'"message":[],"Results":{"series":[]}}'
            ),
        )

    with _client(handler) as client:
        response = BlsPublicClient(
            client,
            _clock=lambda: RECEIVED,
        ).fetch(_request())

    assert len(captured) == 1
    sent = captured[0]
    assert sent.method == "POST"
    assert sent.url.path == "/publicAPI/v1/timeseries/data/"
    assert json.loads(sent.content) == {
        "endyear": "2026",
        "seriesid": ["CUUR0000SA0", "LNS14000000"],
        "startyear": "2025",
    }
    assert response.received_at == RECEIVED
    assert response.content_type == "application/json"


def test_client_rejects_wrong_origin_before_request() -> None:
    with (
        httpx2.Client(
            base_url="https://example.com",
            follow_redirects=False,
        ) as client,
        pytest.raises(BlsPublicTransportError),
    ):
        _ = BlsPublicClient(client)


@pytest.mark.parametrize(
    ("status_code", "headers", "payload"),
    (
        (302, {"location": "https://example.com"}, b""),
        (
            200,
            {
                "content-type": "application/json",
                "content-length": str(BLS_PUBLIC_MAX_RAW_BYTES + 1),
            },
            b"",
        ),
        (
            200,
            {"content-type": "application/json"},
            b"x" * (BLS_PUBLIC_MAX_RAW_BYTES + 1),
        ),
    ),
)
def test_client_rejects_redirect_and_oversized_response(
    status_code: int,
    headers: dict[str, str],
    payload: bytes,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status_code,
            headers=headers,
            stream=httpx2.ByteStream(payload),
            request=request,
        )

    with _client(handler) as client, pytest.raises(BlsPublicTransportError):
        _ = BlsPublicClient(client).fetch(_request())


def _request() -> BlsPublicRequest:
    return BlsPublicRequest(
        collection_id="bls-macro-20260724",
        series_ids=("CUUR0000SA0", "LNS14000000"),
        start_year=2025,
        end_year=2026,
    )


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.Client:
    return httpx2.Client(
        base_url="https://api.bls.gov",
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
