from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx2
import pytest

from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    NonPaperTradingEndpointError,
)
from trading_agent.alpaca_paper_mutation_client import (
    AlpacaPaperMutationClient,
    PaperMutationRejectedError,
    PaperMutationResponseError,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperOrderSide,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoClientOrderId,
    ProtectiveOcoExitPlan,
)
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
)

OBSERVED_AT = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.UTC)


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _plan() -> ProtectiveOcoExitPlan:
    return ProtectiveOcoExitPlan(
        ProtectiveOcoClientOrderId("protect-" + "a" * 40),
        IntentId("intent-1"),
        "AAA",
        PaperOrderSide.SELL,
        10,
        Decimal("10.5"),
        Decimal("9.75"),
    )


def _oco_response() -> dict[str, str | bool | None | list[dict[str, str | bool | None]]]:
    stop = {
        "id": "stop-1",
        "client_order_id": "stop-client-1",
        "symbol": "AAA",
        "side": "sell",
        "status": "new",
        "qty": "10",
        "filled_qty": "0",
        "filled_avg_price": None,
        "limit_price": None,
        "stop_price": "9.75",
        "type": "stop",
        "order_class": "",
        "time_in_force": "day",
        "extended_hours": False,
        "legs": None,
    }
    return {
        "id": "oco-parent-1",
        "client_order_id": _plan().client_order_id,
        "symbol": "AAA",
        "side": "sell",
        "status": "new",
        "qty": "10",
        "filled_qty": "0",
        "filled_avg_price": None,
        "limit_price": "10.5",
        "stop_price": None,
        "type": "limit",
        "order_class": "oco",
        "time_in_force": "day",
        "extended_hours": False,
        "legs": [stop],
    }


def _close_response() -> dict[str, str | bool | None]:
    return {
        "id": "close-1",
        "client_order_id": "close-client-1",
        "symbol": "AAA",
        "side": "sell",
        "status": "accepted",
        "qty": "10",
        "filled_qty": "0",
        "filled_avg_price": None,
        "limit_price": None,
        "stop_price": None,
        "type": "market",
        "order_class": "",
        "time_in_force": "day",
        "extended_hours": False,
    }


def test_mutation_client_rejects_live_endpoint_before_request() -> None:
    def reject(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(str(request.url))

    with (
        httpx2.Client(
            base_url="https://api.alpaca.markets",
            transport=httpx2.MockTransport(reject),
        ) as http_client,
        pytest.raises(NonPaperTradingEndpointError),
    ):
        _ = AlpacaPaperMutationClient(http_client, _credentials())


def test_submit_protective_oco_uses_exact_paper_contract() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/orders"
        assert request.content == (
            b'{"client_order_id":"protect-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            b'"symbol":"AAA","qty":"10","side":"sell","type":"limit",'
            b'"time_in_force":"day","order_class":"oco","extended_hours":false,'
            b'"take_profit":{"limit_price":"10.5"},'
            b'"stop_loss":{"stop_price":"9.75"}}'
        )
        return httpx2.Response(
            200,
            request=request,
            headers={"X-Request-ID": "request-oco-1"},
            json=_oco_response(),
        )

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        receipt = AlpacaPaperMutationClient(
            http_client,
            _credentials(),
            _clock=lambda: OBSERVED_AT,
        ).submit_protective_oco(_plan())

    assert receipt.request_id == "request-oco-1"
    assert receipt.snapshot.observed_at == OBSERVED_AT
    assert receipt.snapshot.take_profit.broker_order_id == "oco-parent-1"
    assert receipt.snapshot.stop_loss.broker_order_id == "stop-1"


def test_cancel_order_requires_204_and_preserves_request_id() -> None:
    action = PaperCancelOrderAction(BrokerOrderId("order-1"), "AAA", False)

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/v2/orders/order-1"
        return httpx2.Response(
            204,
            request=request,
            headers={"X-Request-ID": "request-cancel-1"},
        )

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        receipt = AlpacaPaperMutationClient(
            http_client,
            _credentials(),
            _clock=lambda: OBSERVED_AT,
        ).cancel_order(action)

    assert receipt.request_id == "request-cancel-1"
    assert receipt.broker_order_id == BrokerOrderId("order-1")
    assert receipt.accepted_at == OBSERVED_AT


def test_close_position_uses_exact_quantity_and_validates_returned_order() -> None:
    action = PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal(10))

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/v2/positions/AAA"
        assert request.url.params["qty"] == "10"
        return httpx2.Response(
            200,
            request=request,
            headers={"X-Request-ID": "request-close-1"},
            json=_close_response(),
        )

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        receipt = AlpacaPaperMutationClient(
            http_client,
            _credentials(),
            _clock=lambda: OBSERVED_AT,
        ).close_position(action)

    assert receipt.request_id == "request-close-1"
    assert receipt.order.broker_order_id == BrokerOrderId("close-1")
    assert receipt.order.quantity == Decimal(10)


def test_mutation_response_without_request_id_is_rejected() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request, json=_oco_response())

    with (
        httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
        ) as http_client,
        pytest.raises(PaperMutationResponseError, match="request ID"),
    ):
        _ = AlpacaPaperMutationClient(http_client, _credentials()).submit_protective_oco(_plan())


def test_known_broker_rejection_preserves_status_and_request_id() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            422,
            request=request,
            headers={"X-Request-ID": "request-rejected-1"},
        )

    with (
        httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
        ) as http_client,
        pytest.raises(PaperMutationRejectedError) as captured,
    ):
        _ = AlpacaPaperMutationClient(http_client, _credentials()).submit_protective_oco(_plan())

    assert captured.value.status_code == 422
    assert captured.value.request_id == "request-rejected-1"
