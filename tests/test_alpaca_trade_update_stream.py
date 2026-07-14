from __future__ import annotations

import datetime as dt
import json

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    ALPACA_PAPER_ORDER_STREAM_URL,
    PaperTradeUpdateWireKind,
    authenticate_paper_order_stream,
)


class _Pong:
    def wait(self, timeout: float | None = None) -> bool:
        _ = timeout
        return True


class _Connection:
    final_url = ALPACA_PAPER_ORDER_STREAM_URL

    def __init__(self, responses: list[bytes]) -> None:
        self.responses = responses

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return self.responses.pop(0)

    def ping(self) -> _Pong:
        return _Pong()


def _partial_fill_update() -> bytes:
    return json.dumps(
        {
            "stream": "trade_updates",
            "data": {
                "event": "partial_fill",
                "execution_id": "execution-1",
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
                },
            },
        },
        separators=(",", ":"),
    ).encode()


def test_ready_stream_receives_a_typed_binary_trade_update() -> None:
    connection = _Connection(
        [
            b'{"stream":"authorization","data":{"status":"authorized","action":"authenticate"}}',
            b'{"stream":"listening","data":{"streams":["trade_updates"]}}',
            _partial_fill_update(),
        ]
    )
    stream = authenticate_paper_order_stream(
        connection,
        AlpacaPaperCredentials("test-key", "test-secret"),
        clock=lambda: dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
    )

    update = stream.receive_trade_update(1.0)

    assert update.execution_id == "execution-1"
    assert update.intent_id == "orb-v1-20260714-AAA-093600"
    assert stream.connection_epoch != ""


def test_ready_stream_exposes_the_exact_binary_frame_before_parsing() -> None:
    raw = b"\xff\x00malformed-paper-frame"
    connection = _Connection(
        [
            b'{"stream":"authorization","data":{"status":"authorized","action":"authenticate"}}',
            b'{"stream":"listening","data":{"streams":["trade_updates"]}}',
            raw,
        ]
    )
    stream = authenticate_paper_order_stream(
        connection,
        AlpacaPaperCredentials("test-key", "test-secret"),
        clock=lambda: dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
    )

    frame = stream.receive_trade_update_frame(1.0)

    assert frame.payload == raw
    assert frame.wire_kind is PaperTradeUpdateWireKind.BINARY
