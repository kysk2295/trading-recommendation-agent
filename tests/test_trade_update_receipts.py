from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store
from trading_agent.alpaca_paper_order_stream import (
    PaperTradeUpdateFrame,
    PaperTradeUpdateWireKind,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.trade_update_receipts import (
    InvalidTradeUpdateRawReceiptError,
    TradeUpdateReceiptDisposition,
    TradeUpdateReceiptReason,
)


def test_exact_raw_receipt_is_durable_before_classification(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    frame = PaperTradeUpdateFrame(
        payload=b"\xff\x00malformed-paper-frame",
        wire_kind=PaperTradeUpdateWireKind.BINARY,
    )

    with store.writer() as writer:
        first = writer.save_trade_update_receipt(
            frame,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    restarted = ExecutionStore(store.path)
    receipts = restarted.trade_update_receipts()
    assert receipts == (first,)
    assert receipts[0].raw_payload == frame.payload
    assert receipts[0].wire_kind is PaperTradeUpdateWireKind.BINARY
    assert restarted.pending_trade_update_receipt_keys() == frozenset(
        {first.receipt_key}
    )


def test_raw_replay_deduplicates_within_epoch_but_not_across_epochs(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    frame = PaperTradeUpdateFrame(b'{"same":true}', PaperTradeUpdateWireKind.TEXT)

    with store.writer() as writer:
        first = writer.save_trade_update_receipt(
            frame,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        replay = writer.save_trade_update_receipt(
            frame,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT + dt.timedelta(seconds=1),
        )
        reconnect = writer.save_trade_update_receipt(
            frame,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-2",
            received_at=OBSERVED_AT + dt.timedelta(seconds=2),
        )

    assert replay == first
    assert replay.received_at == OBSERVED_AT.isoformat()
    assert reconnect.receipt_key != first.receipt_key
    assert len(store.trade_update_receipts()) == 2


def test_quarantine_disposition_is_idempotent_and_append_only(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    frame = PaperTradeUpdateFrame(b"not-json", PaperTradeUpdateWireKind.BINARY)
    with store.writer() as writer:
        receipt = writer.save_trade_update_receipt(
            frame,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        first = writer.quarantine_trade_update_receipt(
            receipt.receipt_key,
            reason=TradeUpdateReceiptReason.PROTOCOL_ERROR,
            classified_at=OBSERVED_AT + dt.timedelta(seconds=1),
        )
        replay = writer.quarantine_trade_update_receipt(
            receipt.receipt_key,
            reason=TradeUpdateReceiptReason.PROTOCOL_ERROR,
            classified_at=OBSERVED_AT + dt.timedelta(seconds=2),
        )

    assert first is True
    assert replay is False
    dispositions = store.trade_update_receipt_dispositions()
    assert dispositions[0].disposition is TradeUpdateReceiptDisposition.QUARANTINED
    assert dispositions[0].reason is TradeUpdateReceiptReason.PROTOCOL_ERROR
    assert store.pending_trade_update_receipt_keys() == frozenset()

    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute(
                "UPDATE trade_update_raw_receipts SET raw_payload = X'00'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("DELETE FROM trade_update_receipt_dispositions")


def test_raw_receipt_hash_is_revalidated_on_read(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_trade_update_receipt(
            PaperTradeUpdateFrame(b"original", PaperTradeUpdateWireKind.BINARY),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-hash",
            received_at=OBSERVED_AT,
        )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER trade_update_raw_receipts_no_update")
        connection.execute(
            "UPDATE trade_update_raw_receipts SET raw_payload = X'74616d7065726564'"
        )
        connection.execute(
            "CREATE TRIGGER trade_update_raw_receipts_no_update "
            "BEFORE UPDATE ON trade_update_raw_receipts "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )

    with pytest.raises(InvalidTradeUpdateRawReceiptError, match="raw receipt"):
        _ = store.reconciliation_ledger()
