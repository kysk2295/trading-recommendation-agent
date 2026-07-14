from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType, override

from trading_agent.alpaca_paper_order_stream import PaperTradeUpdateWireKind
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerEventKey,
)

TradeUpdateReceiptKey = NewType("TradeUpdateReceiptKey", str)


class TradeUpdateReceiptDisposition(StrEnum):
    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"


class TradeUpdateReceiptReason(StrEnum):
    PROTOCOL_ERROR = "protocol_error"
    UNKNOWN_INTENT = "unknown_intent"
    ORDER_MISMATCH = "order_mismatch"
    UNEXPECTED_BROKER_ORDER = "unexpected_broker_order"
    IMMUTABLE_CONFLICT = "immutable_conflict"


class TradeUpdateReceiptConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "같은 trade update receipt key의 immutable 필드가 다릅니다"


class UnknownTradeUpdateReceiptError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "분류할 trade update raw receipt가 없습니다"


class InvalidTradeUpdateRawReceiptError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "trade update raw receipt 값이 올바르지 않습니다"


@dataclass(frozen=True, slots=True)
class StoredTradeUpdateReceipt:
    receipt_key: TradeUpdateReceiptKey
    raw_payload_sha256: str
    wire_kind: PaperTradeUpdateWireKind
    raw_payload: bytes
    account_fingerprint: AccountFingerprint
    connection_epoch: str
    received_at: str


@dataclass(frozen=True, slots=True)
class StoredTradeUpdateReceiptDisposition:
    receipt_key: TradeUpdateReceiptKey
    disposition: TradeUpdateReceiptDisposition
    event_key: BrokerEventKey | None
    reason: TradeUpdateReceiptReason | None
    classified_at: str
    recovery_high_water: int
