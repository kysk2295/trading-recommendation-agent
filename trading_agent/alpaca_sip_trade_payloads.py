from __future__ import annotations

import json
from decimal import Decimal

from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipTradeCancelMessage,
    AlpacaSipTradeCorrectionMessage,
    AlpacaSipTradeMessage,
)


def trade_payload(message: AlpacaSipTradeMessage) -> bytes:
    return _payload(
        message.symbol,
        message.trade_id,
        message.exchange,
        message.price,
        message.size,
        message.conditions,
    )


def correction_payload(message: AlpacaSipTradeCorrectionMessage) -> bytes:
    return _payload(
        message.symbol,
        message.corrected_trade_id,
        message.exchange,
        message.corrected_price,
        message.corrected_size,
        message.corrected_conditions,
    )


def cancel_payload(message: AlpacaSipTradeCancelMessage) -> bytes:
    content = {
        "action": message.action,
        "exchange": message.exchange,
        "price": str(message.price),
        "size": message.size,
        "symbol": message.symbol,
        "tape": message.tape,
        "trade_id": message.trade_id,
    }
    return json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def _payload(
    symbol: str,
    trade_id: int,
    exchange: str,
    price: Decimal,
    size: int,
    conditions: tuple[str, ...],
) -> bytes:
    content = {
        "conditions": conditions,
        "exchange": exchange,
        "price": str(price),
        "size": size,
        "symbol": symbol,
        "trade_id": trade_id,
    }
    return json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


__all__ = ("cancel_payload", "correction_payload", "trade_payload")
