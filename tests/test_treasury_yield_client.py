from __future__ import annotations

import httpx2
import pytest

from tests.test_treasury_yield_parser import FIXTURE, RECEIVED, _request
from trading_agent.treasury_yield_client import (
    TREASURY_YIELD_BASE_URL,
    TreasuryYieldClient,
)
from trading_agent.treasury_yield_collection import (
    TreasuryYieldTransportError,
)


def test_client_uses_fixed_official_month_endpoint() -> None:
    # Given
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/xml; charset=utf-8"},
            stream=httpx2.ByteStream(FIXTURE.read_bytes()),
            request=request,
        )

    with httpx2.Client(
        base_url=TREASURY_YIELD_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as http_client:
        client = TreasuryYieldClient(http_client, _clock=lambda: RECEIVED)

        # When
        response = client.fetch(_request())

    # Then
    assert response.raw_payload == FIXTURE.read_bytes()
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET"
    assert request.url.path == ("/resource-center/data-chart-center/interest-rates/pages/xml")
    assert dict(request.url.params) == {
        "data": "daily_treasury_yield_curve",
        "field_tdr_date_value_month": "202607",
    }


@pytest.mark.parametrize(
    "response",
    (
        httpx2.Response(
            302,
            headers={"location": "https://example.com/redirect"},
        ),
        httpx2.Response(
            200,
            headers={
                "content-type": "application/xml",
                "content-length": "1048577",
            },
        ),
    ),
)
def test_redirect_and_oversized_response_are_transport_failures(
    response: httpx2.Response,
) -> None:
    # Given
    with httpx2.Client(
        base_url=TREASURY_YIELD_BASE_URL,
        transport=httpx2.MockTransport(lambda _: response),
        follow_redirects=False,
    ) as http_client:
        client = TreasuryYieldClient(http_client)

        # When/Then
        with pytest.raises(TreasuryYieldTransportError):
            _ = client.fetch(_request())
