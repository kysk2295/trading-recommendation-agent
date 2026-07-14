from __future__ import annotations

import json
from decimal import Decimal

import pytest

from trading_agent.alpaca_trade_updates import (
    AlpacaTradeUpdateProtocolError,
    JsonValue,
    parse_alpaca_trade_update,
)
from trading_agent.paper_execution_models import BrokerOrderEventType, IntentId


def _partial_fill_payload(
    *,
    execution_id: str = "execution-1",
    event_id: str | None = None,
) -> dict[str, JsonValue]:
    data: dict[str, JsonValue] = {
        "event": "partial_fill",
        "execution_id": execution_id,
        "timestamp": "2026-07-14T13:36:01.123456Z",
        "price": "10.05",
        "qty": "10",
        "position_qty": "10",
        "order": {
            "id": "paper-order-1",
            "client_order_id": "orb-v1-20260714-AAA-093600",
            "asset_class": "us_equity",
            "symbol": "AAA",
            "side": "buy",
            "status": "partially_filled",
            "qty": "100",
            "filled_qty": "10",
            "filled_avg_price": "10.05",
            "limit_price": "10.00",
            "time_in_force": "day",
            "extended_hours": False,
            "updated_at": "2026-07-14T13:36:01.223456Z",
            "replaced_by": None,
            "replaces": None,
        },
    }
    if event_id is not None:
        data["event_id"] = event_id
    return {"stream": "trade_updates", "data": data}


def test_partial_fill_is_parsed_into_a_typed_durable_event() -> None:
    update = parse_alpaca_trade_update(json.dumps(_partial_fill_payload()))

    assert update.intent_id == IntentId("orb-v1-20260714-AAA-093600")
    assert update.event_type is BrokerOrderEventType.PARTIAL_FILL
    assert update.execution_id == "execution-1"
    assert update.execution_quantity == Decimal("10")
    assert update.execution_price == Decimal("10.05")
    assert update.position_quantity == Decimal("10")
    assert update.cumulative_filled_quantity == Decimal("10")
    assert update.cumulative_filled_average_price == Decimal("10.05")
    assert update.event_key == "alpaca:execution:execution-1"


def test_execution_id_has_priority_for_fill_deduplication() -> None:
    update = parse_alpaca_trade_update(
        json.dumps(_partial_fill_payload(event_id="01J-ALPACA-EVENT"))
    )

    assert update.event_key == "alpaca:execution:execution-1"


def test_json_formatting_and_key_order_do_not_change_fallback_identity() -> None:
    payload = _partial_fill_payload(execution_id="")
    data = payload["data"]
    assert isinstance(data, dict)
    del data["execution_id"]

    compact = parse_alpaca_trade_update(
        json.dumps(payload, separators=(",", ":"), sort_keys=True)
    )
    indented = parse_alpaca_trade_update(json.dumps(payload, indent=2))

    assert compact.event_key == indented.event_key
    assert compact.payload_json == indented.payload_json
    assert compact.event_key.startswith("alpaca:state:")


def test_two_partial_executions_have_distinct_event_keys() -> None:
    first = parse_alpaca_trade_update(
        json.dumps(_partial_fill_payload(execution_id="execution-1"))
    )
    second = parse_alpaca_trade_update(
        json.dumps(_partial_fill_payload(execution_id="execution-2"))
    )

    assert first.event_key != second.event_key


def test_fallback_identity_preserves_nanosecond_wire_timestamps() -> None:
    first_payload = _partial_fill_payload(execution_id="")
    first_data = first_payload["data"]
    assert isinstance(first_data, dict)
    del first_data["execution_id"]
    first_data["timestamp"] = "2026-07-14T13:36:01.1234561Z"
    second_payload = json.loads(json.dumps(first_payload))
    second_data = second_payload["data"]
    assert isinstance(second_data, dict)
    second_data["timestamp"] = "2026-07-14T13:36:01.1234569Z"

    first = parse_alpaca_trade_update(json.dumps(first_payload))
    second = parse_alpaca_trade_update(json.dumps(second_payload))

    assert first.event_key != second.event_key


def test_fill_without_execution_quantity_fails_closed() -> None:
    payload = _partial_fill_payload()
    data = payload["data"]
    assert isinstance(data, dict)
    del data["qty"]

    with pytest.raises(AlpacaTradeUpdateProtocolError, match="형식"):
        _ = parse_alpaca_trade_update(json.dumps(payload))


def test_fill_event_with_a_nonfilled_order_status_fails_closed() -> None:
    payload = _partial_fill_payload()
    data = payload["data"]
    assert isinstance(data, dict)
    data["event"] = "fill"
    data["qty"] = "100"
    data["position_qty"] = "100"
    order = data["order"]
    assert isinstance(order, dict)
    order["status"] = "canceled"
    order["filled_qty"] = "100"

    with pytest.raises(AlpacaTradeUpdateProtocolError, match="형식"):
        _ = parse_alpaca_trade_update(json.dumps(payload))


def test_non_equity_and_unknown_events_fail_closed() -> None:
    non_equity = _partial_fill_payload()
    non_equity_data = non_equity["data"]
    assert isinstance(non_equity_data, dict)
    order = non_equity_data["order"]
    assert isinstance(order, dict)
    order["asset_class"] = "crypto"

    unknown = _partial_fill_payload()
    unknown_data = unknown["data"]
    assert isinstance(unknown_data, dict)
    unknown_data["event"] = "future_state"

    with pytest.raises(AlpacaTradeUpdateProtocolError):
        _ = parse_alpaca_trade_update(json.dumps(non_equity))
    with pytest.raises(AlpacaTradeUpdateProtocolError):
        _ = parse_alpaca_trade_update(json.dumps(unknown))
