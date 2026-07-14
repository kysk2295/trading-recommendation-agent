from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from trading_agent.alpaca_trade_updates import (
    AlpacaTradeUpdateProtocolError,
    parse_alpaca_trade_update,
)
from trading_agent.execution_errors import (
    TradeUpdateConflictError,
    TradeUpdateOrderMismatchError,
    UnexpectedBrokerOrderIdError,
    UnknownTradeUpdateIntentError,
)
from trading_agent.execution_store import ExecutionWriter
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.trade_update_receipt_models import (
    InvalidTradeUpdateRawReceiptError,
    StoredTradeUpdateReceipt,
    TradeUpdateReceiptKey,
    TradeUpdateReceiptReason,
)


class PaperTradeUpdateIngestionState(StrEnum):
    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class PaperTradeUpdateIngestionResult:
    receipt_key: TradeUpdateReceiptKey
    state: PaperTradeUpdateIngestionState
    event_inserted: bool
    reason: TradeUpdateReceiptReason | None


def classify_committed_trade_update_receipt(
    writer: ExecutionWriter,
    receipt: StoredTradeUpdateReceipt,
    *,
    account_fingerprint: AccountFingerprint,
    classified_at: dt.datetime,
) -> PaperTradeUpdateIngestionResult:
    if classified_at.tzinfo is None or classified_at.utcoffset() is None:
        raise InvalidTradeUpdateRawReceiptError
    received_at = _received_at(receipt, account_fingerprint)
    try:
        update = parse_alpaca_trade_update(receipt.raw_payload)
    except AlpacaTradeUpdateProtocolError:
        _ = writer.quarantine_trade_update_receipt(
            receipt.receipt_key,
            reason=TradeUpdateReceiptReason.PROTOCOL_ERROR,
            classified_at=classified_at,
        )
        return PaperTradeUpdateIngestionResult(
            receipt.receipt_key,
            PaperTradeUpdateIngestionState.QUARANTINED,
            False,
            TradeUpdateReceiptReason.PROTOCOL_ERROR,
        )
    try:
        inserted = writer.append_trade_update(
            update,
            account_fingerprint=account_fingerprint,
            connection_epoch=receipt.connection_epoch,
            received_at=received_at,
        )
    except (
        TradeUpdateConflictError,
        TradeUpdateOrderMismatchError,
        UnexpectedBrokerOrderIdError,
        UnknownTradeUpdateIntentError,
    ) as error:
        reason = _storage_rejection_reason(error)
        _ = writer.quarantine_trade_update_receipt(
            receipt.receipt_key,
            reason=reason,
            classified_at=classified_at,
        )
        return PaperTradeUpdateIngestionResult(
            receipt.receipt_key,
            PaperTradeUpdateIngestionState.QUARANTINED,
            False,
            reason,
        )
    _ = writer.accept_trade_update_receipt(
        receipt.receipt_key,
        update.event_key,
        classified_at=classified_at,
    )
    return PaperTradeUpdateIngestionResult(
        receipt.receipt_key,
        PaperTradeUpdateIngestionState.ACCEPTED,
        inserted,
        None,
    )


def _received_at(
    receipt: StoredTradeUpdateReceipt,
    account_fingerprint: AccountFingerprint,
) -> dt.datetime:
    if receipt.account_fingerprint != account_fingerprint:
        raise InvalidTradeUpdateRawReceiptError
    try:
        received_at = dt.datetime.fromisoformat(receipt.received_at)
    except ValueError as error:
        raise InvalidTradeUpdateRawReceiptError from error
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise InvalidTradeUpdateRawReceiptError
    return received_at


def _storage_rejection_reason(
    error: (
        TradeUpdateConflictError
        | TradeUpdateOrderMismatchError
        | UnexpectedBrokerOrderIdError
        | UnknownTradeUpdateIntentError
    ),
) -> TradeUpdateReceiptReason:
    if isinstance(error, TradeUpdateConflictError):
        return TradeUpdateReceiptReason.IMMUTABLE_CONFLICT
    if isinstance(error, TradeUpdateOrderMismatchError):
        return TradeUpdateReceiptReason.ORDER_MISMATCH
    if isinstance(error, UnexpectedBrokerOrderIdError):
        return TradeUpdateReceiptReason.UNEXPECTED_BROKER_ORDER
    return TradeUpdateReceiptReason.UNKNOWN_INTENT
