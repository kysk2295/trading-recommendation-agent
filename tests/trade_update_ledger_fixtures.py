from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from trading_agent.alpaca_trade_updates import (
    AlpacaTradeUpdate,
    JsonValue,
    parse_alpaca_trade_update,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
)

FINGERPRINT = AccountFingerprint("a" * 64)
OTHER_FINGERPRINT = AccountFingerprint("b" * 64)
OBSERVED_AT = dt.datetime(2026, 7, 14, 13, 36, 2, tzinfo=dt.UTC)


def intent() -> PaperOrderIntent:
    return PaperOrderIntent(
        intent_id=IntentId("orb-v1-20260714-AAA-093600"),
        strategy_id="orb",
        strategy_version="1.0.0",
        symbol="AAA",
        created_at=dt.datetime(2026, 7, 14, 13, 36, tzinfo=dt.UTC),
        side=PaperOrderSide.BUY,
        entry_limit=10.0,
        stop=9.75,
        target_1r=10.25,
        target_2r=10.5,
    )


def trade_update(
    event: str = "partial_fill",
    *,
    status: str = "partially_filled",
    filled_qty: str = "10",
    execution_id: str | None = "execution-1",
    execution_qty: str | None = None,
    order_id: str = "paper-order-1",
    symbol: str = "AAA",
    side: str = "buy",
    order_qty: str = "100",
    limit_price: str = "10.00",
    time_in_force: str = "day",
    extended_hours: bool = False,
    replaced_by: str | None = None,
    replaces: str | None = None,
) -> AlpacaTradeUpdate:
    data: dict[str, JsonValue] = {
        "event": event,
        "event_id": f"event-{event}",
        "timestamp": "2026-07-14T13:36:01.123456Z",
        "order": {
            "id": order_id,
            "client_order_id": intent().intent_id,
            "asset_class": "us_equity",
            "symbol": symbol,
            "side": side,
            "status": status,
            "qty": order_qty,
            "filled_qty": filled_qty,
            "filled_avg_price": "10.05" if filled_qty != "0" else None,
            "limit_price": limit_price,
            "time_in_force": time_in_force,
            "extended_hours": extended_hours,
            "updated_at": "2026-07-14T13:36:01.223456Z",
            "replaced_by": replaced_by,
            "replaces": replaces,
        },
    }
    if execution_id is not None:
        data.update(
            execution_id=execution_id,
            price="10.05",
            qty=filled_qty if execution_qty is None else execution_qty,
            position_qty=filled_qty,
        )
    return parse_alpaca_trade_update(
        json.dumps({"stream": "trade_updates", "data": data})
    )


def initialized_store(tmp_path: Path) -> ExecutionStore:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.bind_account(FINGERPRINT, OBSERVED_AT)
        _ = writer.save_intent(intent(), quantity=100)
    return store
