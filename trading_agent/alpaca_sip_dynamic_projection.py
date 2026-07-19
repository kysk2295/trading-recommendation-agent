from __future__ import annotations

import hashlib
from typing import assert_never, override

from trading_agent.alpaca_sip_dynamic_market_models import (
    AlpacaSipDynamicMarketError,
    AlpacaSipDynamicMarketKind,
    AlpacaSipDynamicWireMessage,
    AlpacaSipProjectedMarketMessage,
    AlpacaSipQuoteMessage,
    parse_alpaca_sip_dynamic_market_frame,
)
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicReceiptKind,
    StoredAlpacaSipDynamicReceipt,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionError,
    AlpacaSipDynamicSubscriptionPlan,
    validate_dynamic_subscription_ack,
)
from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipTradeCancelMessage,
    AlpacaSipTradeCorrectionMessage,
    AlpacaSipTradeMessage,
)
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipControlStage,
    AlpacaSipTradeStreamProtocolError,
    parse_alpaca_sip_control_frame,
)
from trading_agent.us_equity_calendar import NEW_YORK


class AlpacaSipDynamicProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic receipts could not be projected"


def project_alpaca_sip_dynamic_receipts(
    store: AlpacaSipDynamicReceiptStore,
    plan: AlpacaSipDynamicSubscriptionPlan,
    connection_epoch: str,
) -> tuple[AlpacaSipProjectedMarketMessage, ...]:
    try:
        if type(store) is not AlpacaSipDynamicReceiptStore:
            raise AlpacaSipDynamicProjectionError
        replay = store.load_replay(plan, connection_epoch)
        _validate_controls(replay, plan)
        bindings = {item.symbol: item.instrument_id for item in plan.bindings}
        projected: list[AlpacaSipProjectedMarketMessage] = []
        for receipt in replay[3:]:
            if receipt.kind is not AlpacaSipDynamicReceiptKind.DATA:
                raise AlpacaSipDynamicProjectionError
            for index, message in enumerate(parse_alpaca_sip_dynamic_market_frame(receipt.payload)):
                projected.append(_project(message, receipt, index, plan, bindings))
        if not projected:
            raise AlpacaSipDynamicProjectionError
        return tuple(projected)
    except (
        AlpacaSipDynamicMarketError,
        AlpacaSipDynamicReceiptError,
        AlpacaSipDynamicSubscriptionError,
        AlpacaSipTradeStreamProtocolError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise AlpacaSipDynamicProjectionError from None


def _validate_controls(
    replay: tuple[StoredAlpacaSipDynamicReceipt, ...],
    plan: AlpacaSipDynamicSubscriptionPlan,
) -> None:
    if len(replay) < 4 or any(item.kind is not AlpacaSipDynamicReceiptKind.CONTROL for item in replay[:3]):
        raise AlpacaSipDynamicProjectionError
    parse_alpaca_sip_control_frame(replay[0].payload, AlpacaSipControlStage.CONNECTED, plan.symbols[0])
    parse_alpaca_sip_control_frame(replay[1].payload, AlpacaSipControlStage.AUTHENTICATED, plan.symbols[0])
    validate_dynamic_subscription_ack(replay[2].payload, plan)


def _project(
    message: AlpacaSipDynamicWireMessage,
    receipt: StoredAlpacaSipDynamicReceipt,
    message_index: int,
    plan: AlpacaSipDynamicSubscriptionPlan,
    bindings: dict[str, str],
) -> AlpacaSipProjectedMarketMessage:
    instrument_id = bindings.get(message.symbol)
    if (
        instrument_id is None
        or message.timestamp > receipt.received_at
        or message.timestamp.astimezone(NEW_YORK).date() != plan.market_date
    ):
        raise AlpacaSipDynamicProjectionError
    payload = message.model_dump_json(by_alias=True).encode()
    content_sha256 = hashlib.sha256(payload).hexdigest()
    identity = f"{receipt.receipt_id}:{message_index}:{content_sha256}"
    return AlpacaSipProjectedMarketMessage(
        hashlib.sha256(identity.encode()).hexdigest(),
        content_sha256,
        receipt.receipt_id,
        plan.plan_id,
        receipt.connection_epoch,
        receipt.sequence,
        message_index,
        plan.market_date,
        instrument_id,
        message.symbol,
        _kind(message),
        message.timestamp,
        receipt.received_at,
        payload,
    )


def _kind(message: AlpacaSipDynamicWireMessage) -> AlpacaSipDynamicMarketKind:
    match message:
        case AlpacaSipQuoteMessage():
            return AlpacaSipDynamicMarketKind.QUOTE
        case AlpacaSipTradeMessage():
            return AlpacaSipDynamicMarketKind.TRADE
        case AlpacaSipTradeCorrectionMessage():
            return AlpacaSipDynamicMarketKind.CORRECTION
        case AlpacaSipTradeCancelMessage():
            return AlpacaSipDynamicMarketKind.CANCEL
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "AlpacaSipDynamicProjectionError",
    "project_alpaca_sip_dynamic_receipts",
)
