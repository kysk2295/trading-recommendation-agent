from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx2

from trading_agent.alpaca_paper_client import AlpacaPaperClient
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_trade_updates import JsonValue
from trading_agent.paper_protective_oco_models import ProtectiveOcoClientOrderId


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _oco_order_json() -> dict[str, JsonValue]:
    common: dict[str, JsonValue] = {
        "symbol": "AAA",
        "side": "sell",
        "status": "new",
        "qty": "20",
        "filled_qty": "0",
        "filled_avg_price": None,
        "time_in_force": "day",
        "extended_hours": False,
        "created_at": "2026-07-14T13:35:00Z",
        "updated_at": "2026-07-14T13:36:00Z",
        "submitted_at": "2026-07-14T13:35:01Z",
        "filled_at": None,
        "canceled_at": None,
        "failed_at": None,
        "replaced_at": None,
        "replaced_by": None,
        "replaces": None,
        "order_class": "oco",
    }
    stop_leg: dict[str, JsonValue] = {
        **common,
        "id": "paper-stop-1",
        "client_order_id": "paper-stop-client-1",
        "limit_price": None,
        "stop_price": "9.75",
        "type": "stop",
        "legs": None,
    }
    return {
        **common,
        "id": "paper-take-profit-1",
        "client_order_id": "protect-" + "a" * 40,
        "limit_price": "10.5",
        "stop_price": None,
        "type": "limit",
        "legs": [stop_leg],
    }


def test_nested_open_order_inventory_separates_one_oco_from_entries() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, request=request, json=[_oco_order_json()])

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        inventory = AlpacaPaperClient(
            http_client,
            _credentials(),
            _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 1, tzinfo=dt.UTC),
        ).open_order_inventory()

    protection = inventory.protective_ocos[0]
    assert inventory.entry_orders == ()
    assert protection.take_profit.broker_order_id == "paper-take-profit-1"
    assert protection.take_profit.limit_price == Decimal("10.5")
    assert protection.stop_loss.broker_order_id == "paper-stop-1"
    assert protection.stop_loss.stop_price == Decimal("9.75")
    assert requests[0].url.params["nested"] == "true"


def test_recent_order_inventory_retains_oco_without_entry_pollution() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request, json=[_oco_order_json()])

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        inventory = AlpacaPaperClient(
            http_client,
            _credentials(),
            _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 1, tzinfo=dt.UTC),
        ).recent_order_inventory(dt.datetime(2026, 7, 13, tzinfo=dt.UTC))

    assert inventory.entry_orders == ()
    assert len(inventory.protective_ocos) == 1


def test_protective_oco_lookup_by_client_id_requires_nested_structure() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, request=request, json=_oco_order_json())

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as http_client:
        snapshot = AlpacaPaperClient(
            http_client,
            _credentials(),
            _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 1, tzinfo=dt.UTC),
        ).protective_oco_by_client_id(ProtectiveOcoClientOrderId("protect-" + "a" * 40))

    assert snapshot is not None
    assert snapshot.stop_loss.broker_order_id == "paper-stop-1"
    assert requests[0].url.path == "/v2/orders:by_client_order_id"
    assert requests[0].url.params["nested"] == "true"
