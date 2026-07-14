from __future__ import annotations

import datetime as dt

import httpx2
import pytest

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_mutation_client import (
    AlpacaPaperMutationClient,
    PaperMutationResponseError,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
    SizedPaperOrder,
)

OBSERVED_AT = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.UTC)


def _entry() -> SizedPaperOrder:
    return SizedPaperOrder(
        PaperOrderIntent(
            IntentId("orb-v1-20260714-AAA-093600"),
            "orb",
            "1.0.0",
            "AAA",
            OBSERVED_AT,
            PaperOrderSide.BUY,
            10.25,
            10.0,
            10.5,
            10.75,
        ),
        4,
        0.25,
        1.0,
        41.0,
    )


def _entry_response() -> dict[str, str | bool | None]:
    return {
        "id": "entry-1",
        "client_order_id": _entry().intent.intent_id,
        "symbol": "AAA",
        "side": "buy",
        "status": "accepted",
        "qty": "4",
        "filled_qty": "0",
        "filled_avg_price": None,
        "limit_price": "10.25",
        "stop_price": None,
        "type": "limit",
        "order_class": "simple",
        "time_in_force": "day",
        "extended_hours": False,
    }


def test_submit_entry_uses_exact_regular_session_limit_contract() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/orders"
        assert request.content == (
            b'{"client_order_id":"orb-v1-20260714-AAA-093600",'
            b'"symbol":"AAA","qty":"4","side":"buy","type":"limit",'
            b'"time_in_force":"day","order_class":"simple",'
            b'"limit_price":"10.25","extended_hours":false}'
        )
        return httpx2.Response(
            200,
            request=request,
            headers={"X-Request-ID": "request-entry-1"},
            json=_entry_response(),
        )

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        receipt = AlpacaPaperMutationClient(
            http_client,
            AlpacaPaperCredentials("test-key", "test-secret"),
            _clock=lambda: OBSERVED_AT,
        ).submit_entry(_entry())

    assert receipt.request_id == "request-entry-1"
    assert receipt.received_at == OBSERVED_AT
    assert receipt.order.broker_order_id == BrokerOrderId("entry-1")


def test_submit_entry_rejects_response_that_changes_approved_quantity() -> None:
    response = {**_entry_response(), "qty": "5"}

    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            headers={"X-Request-ID": "request-entry-1"},
            json=response,
        )

    with (
        httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
        ) as http_client,
        pytest.raises(PaperMutationResponseError, match="진입 주문 불일치"),
    ):
        _ = AlpacaPaperMutationClient(
            http_client,
            AlpacaPaperCredentials("test-key", "test-secret"),
            _clock=lambda: OBSERVED_AT,
        ).submit_entry(_entry())
