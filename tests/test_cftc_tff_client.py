from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import httpx2
import pytest

from tests.test_cftc_tff_parser import FIXTURE, RECEIVED
from trading_agent.cftc_tff_client import CftcTffClient
from trading_agent.cftc_tff_collection import CftcTffTransportError
from trading_agent.cftc_tff_models import (
    CFTC_TFF_MAX_RAW_BYTES,
    CftcTffRequest,
)


def test_client_uses_fixed_tff_futures_only_query() -> None:
    # Given
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            stream=httpx2.ByteStream(FIXTURE.read_bytes()),
        )

    request = _request()

    # When
    with _client(handler) as client:
        response = CftcTffClient(
            client,
            _clock=lambda: RECEIVED,
        ).fetch(request)

    # Then
    assert len(captured) == 1
    sent = captured[0]
    assert sent.method == "GET"
    assert sent.url.path == "/resource/gpe5-46if.json"
    assert sent.url.params["$limit"] == "2"
    assert sent.url.params["$order"] == ("report_date_as_yyyy_mm_dd DESC")
    assert "cftc_contract_market_code='13874A'" in sent.url.params["$where"]
    assert "futonly_or_combined='FutOnly'" in sent.url.params["$where"]
    assert response.raw_payload == FIXTURE.read_bytes()
    assert response.received_at == RECEIVED


def test_client_rejects_wrong_origin_before_request() -> None:
    # Given
    request = _request()

    # When/Then
    with (
        httpx2.Client(
            base_url="https://example.com",
            follow_redirects=False,
        ) as client,
        pytest.raises(CftcTffTransportError),
    ):
        _ = CftcTffClient(client).fetch(request)


@pytest.mark.parametrize(
    ("status_code", "headers", "payload"),
    (
        (302, {"location": "https://example.com"}, b""),
        (
            200,
            {
                "content-type": "application/json",
                "content-length": str(CFTC_TFF_MAX_RAW_BYTES + 1),
            },
            b"",
        ),
        (
            200,
            {"content-type": "application/json"},
            b"x" * (CFTC_TFF_MAX_RAW_BYTES + 1),
        ),
    ),
)
def test_client_rejects_redirect_and_oversized_response(
    status_code: int,
    headers: dict[str, str],
    payload: bytes,
) -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status_code,
            headers=headers,
            stream=httpx2.ByteStream(payload),
            request=request,
        )

    # When/Then
    with _client(handler) as client, pytest.raises(CftcTffTransportError):
        _ = CftcTffClient(client).fetch(_request())


def _request() -> CftcTffRequest:
    return CftcTffRequest(
        collection_id="es-tff-20260724",
        contract_market_code="13874A",
        through_date=dt.date(2026, 7, 24),
    )


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.Client:
    return httpx2.Client(
        base_url="https://publicreporting.cftc.gov",
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
