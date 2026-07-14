from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx2
import pytest

from trading_agent.alpaca_http import AlpacaApiError
from trading_agent.alpaca_paper_activities import (
    PaperActivityHistoryIncompleteError,
)
from trading_agent.alpaca_paper_client import (
    AlpacaPaperClient,
    PaperOrderListTruncatedError,
)
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.paper_execution_models import BrokerOrderId, IntentId


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


def _fill_activity_json() -> dict[str, str]:
    return {
        "activity_type": "FILL",
        "cum_qty": "2",
        "id": "20260714133600123::execution-1",
        "leaves_qty": "8",
        "price": "10.05",
        "qty": "2",
        "side": "buy",
        "symbol": "AAA",
        "transaction_time": "2026-07-14T13:36:00.123456Z",
        "order_id": "paper-order-1",
        "type": "partial_fill",
    }


def test_fill_activity_read_parses_the_documented_trade_activity_shape() -> None:
    # Given: one Alpaca Paper FILL activity returned after the recovery cursor.
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, request=request, json=[_fill_activity_json()])

    after = dt.datetime(2026, 7, 14, 13, 30, tzinfo=dt.UTC)

    # When: the paper client reads fill activities.
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        activities = AlpacaPaperClient(http_client, _credentials()).fill_activities(after)

    # Then: the typed execution and causal request boundary are preserved.
    assert activities[0].activity_id == "20260714133600123::execution-1"
    assert activities[0].quantity == 2
    assert activities[0].price == Decimal("10.05")
    assert activities[0].event_type == "partial_fill"
    assert requests[0].url.path == "/v2/account/activities/FILL"
    assert requests[0].url.params["after"] == after.isoformat()
    assert requests[0].url.params["direction"] == "asc"
    assert requests[0].url.params["page_size"] == "100"


def test_fill_activity_read_uses_the_last_activity_id_as_page_token() -> None:
    # Given: a full activity page followed by one final entry.
    requests: list[httpx2.Request] = []
    first_page = [
        {
            **_fill_activity_json(),
            "id": f"20260714133600123::execution-{index:03d}",
        }
        for index in range(100)
    ]
    final_page = [
        {
            **_fill_activity_json(),
            "id": "20260714133600124::execution-final",
        }
    ]

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        payload = final_page if "page_token" in request.url.params else first_page
        return httpx2.Response(200, request=request, json=payload)

    # When: the paper client exhausts the ascending FILL history.
    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        activities = AlpacaPaperClient(http_client, _credentials()).fill_activities(
            dt.datetime(2026, 7, 14, 13, 30, tzinfo=dt.UTC)
        )

    # Then: no time cursor is invented and the documented ID cursor advances once.
    assert len(activities) == 101
    assert len(requests) == 2
    assert requests[1].url.params["page_token"] == first_page[-1]["id"]


def test_fill_activity_read_rejects_a_duplicate_across_pages() -> None:
    # Given: Alpaca repeats an activity ID on the next page.
    first_page = [
        {
            **_fill_activity_json(),
            "id": f"20260714133600123::execution-{index:03d}",
        }
        for index in range(100)
    ]

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = [first_page[-1]] if "page_token" in request.url.params else first_page
        return httpx2.Response(200, request=request, json=payload)

    # When / Then: recovery fails instead of silently double-counting a fill.
    with (
        httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
        ) as http_client,
        pytest.raises(PaperActivityHistoryIncompleteError, match="완전하게"),
    ):
        _ = AlpacaPaperClient(http_client, _credentials()).fill_activities(
            dt.datetime(2026, 7, 14, 13, 30, tzinfo=dt.UTC)
        )


def test_order_lookup_returns_none_for_unknown_client_order_id() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v2/orders:by_client_order_id"
        return httpx2.Response(404, request=request, json={"message": "not found"})

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        result = AlpacaPaperClient(http_client, _credentials()).order_by_client_id(IntentId("missing-intent"))

    assert result is None


def test_order_lookup_by_broker_id_preserves_terminal_cancel_state() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            json={**_order_json(), "status": "canceled"},
        )

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        order = AlpacaPaperClient(http_client, _credentials()).order_by_id(BrokerOrderId("paper-order-1"))

    assert order is not None
    assert order.status == "canceled"
    assert requests[0].url.path == "/v2/orders/paper-order-1"


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

    with (
        httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
        ) as http_client,
        pytest.raises(PaperOrderListTruncatedError, match="500"),
    ):
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

    with (
        httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
        ) as http_client,
        pytest.raises(AlpacaApiError, match="주문 목록 형식"),
    ):
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
        orders = AlpacaPaperClient(http_client, _credentials()).recent_orders(dt.datetime(2026, 7, 13, tzinfo=dt.UTC))

    assert len(orders) == 501
    assert len(requests) == 2
    assert requests[0].url.params["status"] == "all"
    assert requests[0].url.params["nested"] == "true"
    assert "after" not in requests[0].url.params
    assert "until" not in requests[0].url.params
    assert requests[1].url.params["before_order_id"] == "paper-order-499"
