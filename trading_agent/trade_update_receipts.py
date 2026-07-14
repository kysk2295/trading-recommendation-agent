from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3

from trading_agent.alpaca_paper_order_stream import (
    PaperTradeUpdateFrame,
    PaperTradeUpdateWireKind,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerEventKey,
)
from trading_agent.trade_update_receipt_models import (
    InvalidTradeUpdateRawReceiptError,
    StoredTradeUpdateReceipt,
    StoredTradeUpdateReceiptDisposition,
    TradeUpdateReceiptConflictError,
    TradeUpdateReceiptDisposition,
    TradeUpdateReceiptKey,
    TradeUpdateReceiptReason,
    UnknownTradeUpdateReceiptError,
)

type RawReceiptRow = tuple[str, str, str, bytes, str, str, str]
type DispositionRow = tuple[str, str, str | None, str | None, str, int]


def save_trade_update_receipt(
    connection: sqlite3.Connection,
    frame: PaperTradeUpdateFrame,
    *,
    account_fingerprint: AccountFingerprint,
    connection_epoch: str,
    received_at: dt.datetime,
) -> StoredTradeUpdateReceipt:
    _require_receipt_values(frame, account_fingerprint, connection_epoch, received_at)
    payload_hash = hashlib.sha256(frame.payload).hexdigest()
    receipt_key = _receipt_key(
        account_fingerprint,
        connection_epoch,
        frame.wire_kind,
        payload_hash,
    )
    existing = _raw_receipt(connection, receipt_key)
    if existing is not None:
        if (
            existing.raw_payload_sha256 != payload_hash
            or existing.wire_kind is not frame.wire_kind
            or existing.raw_payload != frame.payload
            or existing.account_fingerprint != account_fingerprint
            or existing.connection_epoch != connection_epoch
        ):
            raise TradeUpdateReceiptConflictError
        return existing
    values = (
        receipt_key,
        payload_hash,
        frame.wire_kind.value,
        sqlite3.Binary(frame.payload),
        account_fingerprint,
        connection_epoch,
        received_at.isoformat(),
    )
    _ = connection.execute(
        "INSERT INTO trade_update_raw_receipts VALUES (?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    connection.commit()
    stored = _raw_receipt(connection, receipt_key)
    if stored is None:
        raise UnknownTradeUpdateReceiptError
    return stored


def classify_trade_update_receipt(
    connection: sqlite3.Connection,
    receipt_key: TradeUpdateReceiptKey,
    *,
    disposition: TradeUpdateReceiptDisposition,
    event_key: BrokerEventKey | None,
    reason: TradeUpdateReceiptReason | None,
    classified_at: dt.datetime,
) -> bool:
    if not _is_aware(classified_at):
        raise InvalidTradeUpdateRawReceiptError
    if _raw_receipt(connection, receipt_key) is None:
        raise UnknownTradeUpdateReceiptError
    existing = _disposition(connection, receipt_key)
    if existing is not None:
        if (
            existing.disposition is not disposition
            or existing.event_key != event_key
            or existing.reason is not reason
        ):
            raise TradeUpdateReceiptConflictError
        return False
    high_water_row: tuple[int] | None = connection.execute(
        "SELECT COALESCE(MAX(rowid), 0) FROM paper_stream_recoveries"
    ).fetchone()
    recovery_high_water = 0 if high_water_row is None else high_water_row[0]
    _ = connection.execute(
        """INSERT INTO trade_update_receipt_dispositions
        (receipt_key, disposition, event_key, reason_code, classified_at,
        recovery_high_water)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (
            receipt_key,
            disposition.value,
            event_key,
            None if reason is None else reason.value,
            classified_at.isoformat(),
            recovery_high_water,
        ),
    )
    connection.commit()
    return True


def read_trade_update_receipts(
    connection: sqlite3.Connection,
) -> tuple[StoredTradeUpdateReceipt, ...]:
    rows: list[RawReceiptRow] = connection.execute(
        "SELECT * FROM trade_update_raw_receipts ORDER BY rowid"
    ).fetchall()
    return tuple(_stored_raw_receipt(row) for row in rows)


def read_trade_update_receipt_dispositions(
    connection: sqlite3.Connection,
) -> tuple[StoredTradeUpdateReceiptDisposition, ...]:
    rows: list[DispositionRow] = connection.execute(
        "SELECT * FROM trade_update_receipt_dispositions ORDER BY rowid"
    ).fetchall()
    return tuple(_stored_disposition(row) for row in rows)


def pending_trade_update_receipt_keys(
    connection: sqlite3.Connection,
) -> frozenset[TradeUpdateReceiptKey]:
    rows: list[tuple[str]] = connection.execute(
        """SELECT raw.receipt_key FROM trade_update_raw_receipts AS raw
        LEFT JOIN trade_update_receipt_dispositions AS disposition
        ON disposition.receipt_key = raw.receipt_key
        WHERE disposition.receipt_key IS NULL"""
    ).fetchall()
    return frozenset(TradeUpdateReceiptKey(row[0]) for row in rows)


def _raw_receipt(
    connection: sqlite3.Connection,
    receipt_key: TradeUpdateReceiptKey,
) -> StoredTradeUpdateReceipt | None:
    row: RawReceiptRow | None = connection.execute(
        "SELECT * FROM trade_update_raw_receipts WHERE receipt_key = ?",
        (receipt_key,),
    ).fetchone()
    return None if row is None else _stored_raw_receipt(row)


def _disposition(
    connection: sqlite3.Connection,
    receipt_key: TradeUpdateReceiptKey,
) -> StoredTradeUpdateReceiptDisposition | None:
    row: DispositionRow | None = connection.execute(
        "SELECT * FROM trade_update_receipt_dispositions WHERE receipt_key = ?",
        (receipt_key,),
    ).fetchone()
    return None if row is None else _stored_disposition(row)


def _stored_raw_receipt(row: RawReceiptRow) -> StoredTradeUpdateReceipt:
    receipt_key = TradeUpdateReceiptKey(row[0])
    wire_kind = PaperTradeUpdateWireKind(row[2])
    payload = bytes(row[3])
    account_fingerprint = AccountFingerprint(row[4])
    payload_hash = hashlib.sha256(payload).hexdigest()
    try:
        received_at = dt.datetime.fromisoformat(row[6])
    except ValueError as error:
        raise InvalidTradeUpdateRawReceiptError from error
    if (
        row[1] != payload_hash
        or receipt_key
        != _receipt_key(account_fingerprint, row[5], wire_kind, payload_hash)
        or not _is_aware(received_at)
    ):
        raise InvalidTradeUpdateRawReceiptError
    return StoredTradeUpdateReceipt(
        receipt_key,
        payload_hash,
        wire_kind,
        payload,
        account_fingerprint,
        row[5],
        row[6],
    )


def _stored_disposition(row: DispositionRow) -> StoredTradeUpdateReceiptDisposition:
    return StoredTradeUpdateReceiptDisposition(
        TradeUpdateReceiptKey(row[0]),
        TradeUpdateReceiptDisposition(row[1]),
        None if row[2] is None else BrokerEventKey(row[2]),
        None if row[3] is None else TradeUpdateReceiptReason(row[3]),
        row[4],
        row[5],
    )


def _receipt_key(
    account_fingerprint: AccountFingerprint,
    connection_epoch: str,
    wire_kind: PaperTradeUpdateWireKind,
    payload_hash: str,
) -> TradeUpdateReceiptKey:
    material = "\x00".join(
        (account_fingerprint, connection_epoch, wire_kind.value, payload_hash)
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    return TradeUpdateReceiptKey(f"alpaca:raw:{digest}")


def _require_receipt_values(
    frame: PaperTradeUpdateFrame,
    account_fingerprint: AccountFingerprint,
    connection_epoch: str,
    received_at: dt.datetime,
) -> None:
    if not account_fingerprint or not connection_epoch or not _is_aware(received_at):
        raise InvalidTradeUpdateRawReceiptError


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
