from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.paper_stream_recovery_fixtures import recovery
from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
)
from trading_agent.alpaca_paper_order_stream import (
    PaperTradeUpdateFrame,
    PaperTradeUpdateWireKind,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    PaperOrderSide,
    PaperOrderSnapshot,
)
from trading_agent.paper_stream_recovery import (
    InvalidPaperStreamRecoveryError,
    PaperRecoveryOrderObservation,
    PaperRecoveryOrderSource,
    PaperStreamRecoveryConflictError,
    PaperStreamRecoveryObservation,
)
from trading_agent.trade_update_receipts import TradeUpdateReceiptReason


def test_only_a_recovery_completed_after_quarantine_resolves_it(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    before = OBSERVED_AT - dt.timedelta(seconds=2)
    quarantine_at = OBSERVED_AT
    frame = PaperTradeUpdateFrame(b"unknown", PaperTradeUpdateWireKind.BINARY)
    with store.writer() as writer:
        receipt = writer.save_trade_update_receipt(
            frame,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=quarantine_at,
        )
        _ = writer.quarantine_trade_update_receipt(
            receipt.receipt_key,
            reason=TradeUpdateReceiptReason.PROTOCOL_ERROR,
            classified_at=quarantine_at,
        )
        _ = writer.append_paper_stream_recovery(
            recovery(
                epoch="epoch-before",
                started_at=before - dt.timedelta(seconds=1),
                completed_at=before,
            )
        )

    first_ledger = store.reconciliation_ledger()
    assert first_ledger.unrecovered_trade_update_quarantine_keys == frozenset(
        {receipt.receipt_key}
    )

    with store.writer() as writer:
        inserted = writer.append_paper_stream_recovery(
            recovery(
                epoch="epoch-2",
                started_at=quarantine_at + dt.timedelta(seconds=1),
                completed_at=quarantine_at + dt.timedelta(seconds=2),
            )
        )
        replay = writer.append_paper_stream_recovery(
            recovery(
                epoch="epoch-2",
                started_at=quarantine_at + dt.timedelta(seconds=1),
                completed_at=quarantine_at + dt.timedelta(seconds=2),
            )
        )

    assert inserted is True
    assert replay is False
    assert store.reconciliation_ledger().unrecovered_trade_update_quarantine_keys == frozenset()
    assert len(store.paper_stream_recoveries()) == 2


def test_pending_raw_receipt_is_not_resolved_by_a_later_recovery(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        receipt = writer.save_trade_update_receipt(
            PaperTradeUpdateFrame(b"pending", PaperTradeUpdateWireKind.BINARY),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        _ = writer.append_paper_stream_recovery(
            recovery(
                epoch="epoch-2",
                started_at=OBSERVED_AT + dt.timedelta(seconds=1),
                completed_at=OBSERVED_AT + dt.timedelta(seconds=2),
            )
        )

    ledger = store.reconciliation_ledger()
    assert ledger.pending_trade_update_receipt_keys == frozenset(
        {receipt.receipt_key}
    )


def test_immutable_conflict_quarantine_is_not_cleared_by_aggregate_recovery(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        receipt = writer.save_trade_update_receipt(
            PaperTradeUpdateFrame(b"conflict", PaperTradeUpdateWireKind.BINARY),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-conflict",
            received_at=OBSERVED_AT,
        )
        _ = writer.quarantine_trade_update_receipt(
            receipt.receipt_key,
            reason=TradeUpdateReceiptReason.IMMUTABLE_CONFLICT,
            classified_at=OBSERVED_AT,
        )
        _ = writer.append_paper_stream_recovery(
            recovery(
                epoch="epoch-after-conflict",
                started_at=OBSERVED_AT + dt.timedelta(seconds=1),
                completed_at=OBSERVED_AT + dt.timedelta(seconds=2),
            )
        )

    assert store.reconciliation_ledger().unrecovered_trade_update_quarantine_keys == (
        frozenset({receipt.receipt_key})
    )


def test_same_recovery_bracket_with_changed_snapshot_fails_closed(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    observation = recovery(
        epoch="epoch-1",
        started_at=OBSERVED_AT,
        completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
    )
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(observation)
        with pytest.raises(PaperStreamRecoveryConflictError, match="immutable"):
            _ = writer.append_paper_stream_recovery(
                replace(observation, snapshot_json='{"changed":true}')
            )


def test_recovery_ledger_is_append_only(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(
            recovery(
                epoch="epoch-1",
                started_at=OBSERVED_AT,
                completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
            )
        )

    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute(
                "UPDATE paper_stream_recoveries SET connection_epoch = 'changed'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("DELETE FROM paper_stream_recoveries")


def test_recovery_snapshot_hash_is_revalidated_on_read(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(
            recovery(
                epoch="epoch-hash",
                started_at=OBSERVED_AT,
                completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
            )
        )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER paper_stream_recoveries_no_update")
        connection.execute(
            "UPDATE paper_stream_recoveries SET snapshot_json = '{\"tampered\":true}'"
        )
        connection.execute(
            "CREATE TRIGGER paper_stream_recoveries_no_update "
            "BEFORE UPDATE ON paper_stream_recoveries "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )

    with pytest.raises(InvalidPaperStreamRecoveryError, match="복구 증거"):
        _ = store.reconciliation_ledger()


def test_recovery_order_hash_is_revalidated_on_read(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    order = PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        store.intents()[0].intent_id,
        "AAA",
        PaperOrderSide.BUY,
        "accepted",
        Decimal(100),
        Decimal(0),
        Decimal(10),
        "day",
        False,
    )
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(
            PaperStreamRecoveryObservation(
                FINGERPRINT,
                "epoch-order-hash",
                OBSERVED_AT,
                OBSERVED_AT + dt.timedelta(seconds=1),
                '{"orders":1}',
                True,
                (
                    PaperRecoveryOrderObservation(
                        PaperRecoveryOrderSource.TARGETED,
                        order,
                    ),
                ),
            )
        )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER paper_recovery_orders_no_update")
        connection.execute("UPDATE paper_recovery_orders SET status = 'canceled'")
        connection.execute(
            "CREATE TRIGGER paper_recovery_orders_no_update "
            "BEFORE UPDATE ON paper_recovery_orders "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )

    with pytest.raises(InvalidPaperStreamRecoveryError, match="복구 증거"):
        _ = store.reconciliation_ledger()
