from __future__ import annotations

import datetime as dt

import httpx2
import pytest

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_client import (
    AlpacaPaperClient,
    PaperOrderListTruncatedError,
)
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.paper_execution_models import IntentId


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _order_json() -> dict[str, str | bool | None]:
    return {
        "id": "paper-order-1",
        "client_order_id": "orb-v1-20260714-AAA-093600",
        "symbol": "AAA",
        "side": "buy",
        "status": "partially_filled",
        "qty": "259",
        "filled_qty": "0.5",
        "filled_avg_price": "10.05",
        "limit_price": "10.0000",
        "time_in_force": "day",
        "extended_hours": False,
        "created_at": "2026-07-14T13:35:00Z",
        "updated_at": "2026-07-14T13:36:00.123456789Z",
        "submitted_at": "2026-07-14T13:35:01Z",
        "filled_at": None,
        "canceled_at": None,
        "failed_at": None,
        "replaced_at": None,
        "replaced_by": "paper-order-2",
        "replaces": None,
    }


def test_order_lookup_returns_none_for_unknown_client_order_id() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v2/orders:by_client_order_id"
        return httpx2.Response(404, request=request, json={"message": "not found"})

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        result = AlpacaPaperClient(http_client, _credentials()).order_by_client_id(
            IntentId("missing-intent")
        )

    assert result is None


def test_open_order_page_at_the_documented_maximum_fails_closed() -> None:
    orders = [
        {
            **_order_json(),
            "id": f"paper-order-{index}",
            "client_order_id": f"intent-{index}",
        }
        for index in range(500)
    ]

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["limit"] == "500"
        return httpx2.Response(200, request=request, json=orders)

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client, pytest.raises(PaperOrderListTruncatedError, match="500"):
        _ = AlpacaPaperClient(http_client, _credentials()).open_orders()


def test_order_read_rejects_a_partial_quantity_with_filled_status() -> None:
    payload = {
        **_order_json(),
        "status": "filled",
        "qty": "10",
        "filled_qty": "5",
    }

    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request, json=[payload])

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client, pytest.raises(AlpacaApiError, match="주문 목록 형식"):
        _ = AlpacaPaperClient(http_client, _credentials()).open_orders()


def test_recent_order_history_pages_with_before_order_id_without_time_cursor() -> None:
    requests: list[httpx2.Request] = []
    first_page = [
        {
            **_order_json(),
            "id": f"paper-order-{index}",
            "client_order_id": f"intent-{index}",
        }
        for index in range(500)
    ]
    second_page = [
        {
            **_order_json(),
            "id": "paper-order-older",
            "client_order_id": "intent-older",
        }
    ]

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        page = second_page if "before_order_id" in request.url.params else first_page
        return httpx2.Response(200, request=request, json=page)

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        orders = AlpacaPaperClient(http_client, _credentials()).recent_orders(
            dt.datetime(2026, 7, 13, tzinfo=dt.UTC)
        )

    assert len(orders) == 501
    assert len(requests) == 2
    assert requests[0].url.params["status"] == "all"
    assert requests[0].url.params["nested"] == "false"
    assert "after" not in requests[0].url.params
    assert "until" not in requests[0].url.params
    assert requests[1].url.params["before_order_id"] == "paper-order-499"
