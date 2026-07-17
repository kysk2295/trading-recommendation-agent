from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path
from typing import Never, cast

import pytest

from trading_agent.alpaca_paper_order_stream import (
    PaperTradeUpdateFrame,
    PaperTradeUpdateWireKind,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.execution_store_reader import ExecutionStoreReader
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_trade_update_raw_projection import (
    InvalidPaperTradeUpdateRawReceiptProjectionError,
    project_paper_trade_update_receipts,
)
from trading_agent.trade_update_receipt_models import (
    InvalidTradeUpdateRawReceiptError,
    TradeUpdateRawReceiptProjectionRecord,
    TradeUpdateReceiptProjectionSnapshot,
)

MARKET_DATE = dt.date(2026, 7, 17)
PRIVATE_ACCOUNT = AccountFingerprint("private-paper-account-fingerprint")
PRIVATE_EPOCH = "private-connection-epoch"
PRIVATE_PAYLOAD = b'{"private":"paper-trade-update-raw-payload"}'


def test_projects_selected_paper_receipts_with_redacted_snapshot_and_manifest(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    first = _save_raw_receipt(
        store,
        PRIVATE_PAYLOAD,
        connection_epoch=PRIVATE_EPOCH,
        received_at=dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC),
    )
    second = _save_raw_receipt(
        store,
        PRIVATE_PAYLOAD,
        connection_epoch="private-connection-epoch-2",
        received_at=dt.datetime(2026, 7, 17, 13, 31, tzinfo=dt.UTC),
    )

    snapshot = store.trade_update_receipt_projection_snapshot(market_date=MARKET_DATE)
    manifest = project_paper_trade_update_receipts(store, market_date=MARKET_DATE)

    assert type(snapshot) is TradeUpdateReceiptProjectionSnapshot
    assert all(type(receipt) is TradeUpdateRawReceiptProjectionRecord for receipt in snapshot.receipts)
    assert snapshot.parent_ledger_generation == 2
    assert tuple(receipt.receipt_id for receipt in snapshot.receipts) == (
        first.receipt_key.removeprefix("alpaca:raw:"),
        second.receipt_key.removeprefix("alpaca:raw:"),
    )
    assert snapshot.receipts[0].receipt_id != snapshot.receipts[1].receipt_id
    assert not hasattr(snapshot.receipts[0], "account_fingerprint")
    assert not hasattr(snapshot.receipts[0], "connection_epoch")
    assert not hasattr(snapshot.receipts[0], "wire_kind")
    assert not hasattr(snapshot.receipts[0], "receipt_key")
    assert manifest is not None
    assert manifest.source_id == "us.alpaca.paper.trade_updates"
    assert manifest.market_date == MARKET_DATE
    assert manifest.parent_ledger_generation == 2
    assert tuple(item.receipt_id for item in manifest.receipts) == tuple(
        sorted(receipt.receipt_id for receipt in snapshot.receipts)
    )
    for private_value in (
        PRIVATE_ACCOUNT,
        PRIVATE_EPOCH,
        PRIVATE_PAYLOAD.decode(),
        "alpaca:raw:",
    ):
        assert private_value not in repr(snapshot)
        assert private_value not in repr(manifest)
        assert private_value not in manifest.model_dump_json()


def test_projects_501_selected_receipts_with_sqlite_variable_limit(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    with store.writer() as writer:
        for index in range(501):
            _ = writer.save_trade_update_receipt(
                PaperTradeUpdateFrame(PRIVATE_PAYLOAD, PaperTradeUpdateWireKind.BINARY),
                account_fingerprint=PRIVATE_ACCOUNT,
                connection_epoch=f"chunked-private-epoch-{index}",
                received_at=dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC),
            )

    manifest = project_paper_trade_update_receipts(
        _FiveHundredVariableLimitReader(store.path),
        market_date=MARKET_DATE,
    )

    assert manifest is not None
    assert manifest.receipt_count == 501
    assert manifest.parent_ledger_generation == 501
    assert PRIVATE_PAYLOAD.decode() not in repr(manifest)
    assert PRIVATE_PAYLOAD.decode() not in manifest.model_dump_json()
    assert PRIVATE_ACCOUNT not in manifest.model_dump_json()
    assert "chunked-private-epoch-500" not in manifest.model_dump_json()


def test_snapshot_uses_new_york_date_and_keeps_selected_row_high_water_stable(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    _ = _save_raw_receipt(
        store,
        b"prior-other-market-date",
        connection_epoch="prior-epoch",
        received_at=dt.datetime(2026, 7, 17, 3, 59, tzinfo=dt.UTC),
    )
    selected = _save_raw_receipt(
        store,
        b"selected-market-date",
        connection_epoch="selected-epoch",
        received_at=dt.datetime(2026, 7, 18, 3, 59, tzinfo=dt.UTC),
    )

    before = project_paper_trade_update_receipts(store, market_date=MARKET_DATE)
    later = _save_raw_receipt(
        store,
        b"later-other-market-date",
        connection_epoch="later-epoch",
        received_at=dt.datetime(2026, 7, 18, 4, 0, tzinfo=dt.UTC),
    )
    _replace_raw_payload(store, later.receipt_key, b"tampered-unselected-raw-payload")
    after_snapshot = store.trade_update_receipt_projection_snapshot(market_date=MARKET_DATE)
    after = project_paper_trade_update_receipts(store, market_date=MARKET_DATE)

    assert before is not None
    assert before.parent_ledger_generation == 2
    assert tuple(item.receipt_id for item in before.receipts) == (selected.receipt_key.removeprefix("alpaca:raw:"),)
    assert after_snapshot.parent_ledger_generation == 2
    assert tuple(receipt.receipt_id for receipt in after_snapshot.receipts) == (
        selected.receipt_key.removeprefix("alpaca:raw:"),
    )
    assert after == before


def test_reader_rejects_malformed_unselected_timestamp_metadata(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    receipt = _save_raw_receipt(
        store,
        b"other-market-date",
        connection_epoch="other-epoch",
        received_at=dt.datetime(2026, 7, 18, 4, 0, tzinfo=dt.UTC),
    )
    _replace_received_at(store, receipt.receipt_key, "malformed-private-timestamp")

    with pytest.raises(InvalidTradeUpdateRawReceiptError, match="raw receipt"):
        _ = store.trade_update_receipt_projection_snapshot(market_date=MARKET_DATE)


def test_empty_snapshot_and_missing_store_project_to_none(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "missing-execution.sqlite3")

    snapshot = store.trade_update_receipt_projection_snapshot(market_date=MARKET_DATE)

    assert snapshot == TradeUpdateReceiptProjectionSnapshot((), 0)
    assert project_paper_trade_update_receipts(store, market_date=MARKET_DATE) is None


@pytest.mark.parametrize(
    "reader",
    (
        cast(ExecutionStoreReader, object()),
        cast(ExecutionStoreReader, None),
    ),
)
def test_projection_rejects_invalid_reader(
    tmp_path: Path,
    reader: ExecutionStoreReader,
) -> None:
    error = _projection_error(reader)

    assert str(error) == "paper trade update raw receipt projection is invalid"
    assert error.__cause__ is None


@pytest.mark.parametrize(
    "market_date",
    (
        dt.datetime(2026, 7, 17, tzinfo=dt.UTC),
        "2026-07-17",
    ),
)
def test_projection_rejects_invalid_market_date(tmp_path: Path, market_date: object) -> None:
    error = _projection_error(_initialized_store(tmp_path), market_date=market_date)

    assert str(error) == "paper trade update raw receipt projection is invalid"
    assert error.__cause__ is None


def test_reader_rejects_datetime_market_date(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)

    with pytest.raises(InvalidTradeUpdateRawReceiptError, match="raw receipt"):
        _ = store.trade_update_receipt_projection_snapshot(
            market_date=dt.datetime(2026, 7, 17, tzinfo=dt.UTC),  # type: ignore[arg-type]
        )


def test_adapter_uses_snapshot_instead_of_legacy_trade_update_receipts(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    _ = _save_raw_receipt(
        store,
        b"selected-market-date",
        connection_epoch="selected-epoch",
        received_at=dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC),
    )

    manifest = project_paper_trade_update_receipts(
        _LegacyTradeUpdateReceiptsForbiddenReader(store.path),
        market_date=MARKET_DATE,
    )

    assert manifest is not None
    assert manifest.source_id == "us.alpaca.paper.trade_updates"
    assert manifest.receipt_count == 1


def test_adapter_sanitizes_malformed_snapshot_and_receipt_key(tmp_path: Path) -> None:
    private_key = "alpaca:raw:" + "a" * 64
    malformed_record = TradeUpdateRawReceiptProjectionRecord(
        receipt_id=private_key,
        received_at=dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC),
        payload_sha256=hashlib.sha256(PRIVATE_PAYLOAD).hexdigest(),
        raw_payload=PRIVATE_PAYLOAD,
    )
    reader = _SnapshotReader(
        tmp_path / "unused.sqlite3",
        TradeUpdateReceiptProjectionSnapshot((malformed_record,), 1),
    )

    error = _projection_error(reader)

    assert str(error) == "paper trade update raw receipt projection is invalid"
    assert private_key not in str(error)
    assert PRIVATE_PAYLOAD.decode() not in str(error)
    assert error.__cause__ is None


def test_adapter_sanitizes_selected_raw_blob_integrity_failure(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    receipt = _save_raw_receipt(
        store,
        PRIVATE_PAYLOAD,
        connection_epoch=PRIVATE_EPOCH,
        received_at=dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC),
    )
    _replace_raw_payload(store, receipt.receipt_key, b"tampered-selected-raw-payload")

    error = _projection_error(store)

    assert str(error) == "paper trade update raw receipt projection is invalid"
    assert PRIVATE_PAYLOAD.decode() not in str(error)
    assert PRIVATE_ACCOUNT not in str(error)
    assert PRIVATE_EPOCH not in str(error)
    assert receipt.receipt_key not in str(error)
    assert error.__cause__ is None


def test_adapter_sanitizes_selected_non_blob_raw_payload(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path)
    receipt = _save_raw_receipt(
        store,
        PRIVATE_PAYLOAD,
        connection_epoch=PRIVATE_EPOCH,
        received_at=dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC),
    )
    _replace_raw_payload_with_integer_consistently(
        store,
        receipt_key=receipt.receipt_key,
        connection_epoch=PRIVATE_EPOCH,
    )

    error = _projection_error(store)

    assert str(error) == "paper trade update raw receipt projection is invalid"
    assert PRIVATE_PAYLOAD.decode() not in str(error)
    assert PRIVATE_ACCOUNT not in str(error)
    assert PRIVATE_EPOCH not in str(error)
    assert receipt.receipt_key not in str(error)
    assert error.__cause__ is None


def _initialized_store(tmp_path: Path) -> ExecutionStore:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.bind_account(PRIVATE_ACCOUNT, dt.datetime(2026, 7, 17, 13, 0, tzinfo=dt.UTC))
    return store


def _save_raw_receipt(
    store: ExecutionStore,
    payload: bytes,
    *,
    connection_epoch: str,
    received_at: dt.datetime,
):
    with store.writer() as writer:
        return writer.save_trade_update_receipt(
            PaperTradeUpdateFrame(payload, PaperTradeUpdateWireKind.BINARY),
            account_fingerprint=PRIVATE_ACCOUNT,
            connection_epoch=connection_epoch,
            received_at=received_at,
        )


def _replace_raw_payload(store: ExecutionStore, receipt_key: str, payload: bytes) -> None:
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER trade_update_raw_receipts_no_update")
        connection.execute(
            "UPDATE trade_update_raw_receipts SET raw_payload = ? WHERE receipt_key = ?",
            (sqlite3.Binary(payload), receipt_key),
        )
        connection.execute(
            "CREATE TRIGGER trade_update_raw_receipts_no_update "
            "BEFORE UPDATE ON trade_update_raw_receipts "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )


def _replace_raw_payload_with_integer_consistently(
    store: ExecutionStore,
    *,
    receipt_key: str,
    connection_epoch: str,
) -> None:
    non_blob_payload = 0
    payload_bytes = bytes(non_blob_payload)
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    receipt_material = "\x00".join(
        (PRIVATE_ACCOUNT, connection_epoch, PaperTradeUpdateWireKind.BINARY.value, payload_sha256)
    )
    replacement_receipt_key = f"alpaca:raw:{hashlib.sha256(receipt_material.encode()).hexdigest()}"
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER trade_update_raw_receipts_no_update")
        connection.execute(
            "UPDATE trade_update_raw_receipts "
            "SET receipt_key = ?, raw_payload_sha256 = ?, raw_payload = ? "
            "WHERE receipt_key = ?",
            (replacement_receipt_key, payload_sha256, non_blob_payload, receipt_key),
        )
        row = connection.execute(
            "SELECT receipt_key, raw_payload_sha256, typeof(raw_payload) "
            "FROM trade_update_raw_receipts WHERE receipt_key = ?",
            (replacement_receipt_key,),
        ).fetchone()
        assert row == (replacement_receipt_key, payload_sha256, "integer")
        connection.execute(
            "CREATE TRIGGER trade_update_raw_receipts_no_update "
            "BEFORE UPDATE ON trade_update_raw_receipts "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )


def _replace_received_at(store: ExecutionStore, receipt_key: str, received_at: str) -> None:
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER trade_update_raw_receipts_no_update")
        connection.execute(
            "UPDATE trade_update_raw_receipts SET received_at = ? WHERE receipt_key = ?",
            (received_at, receipt_key),
        )
        connection.execute(
            "CREATE TRIGGER trade_update_raw_receipts_no_update "
            "BEFORE UPDATE ON trade_update_raw_receipts "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )


def _projection_error(
    reader: ExecutionStoreReader,
    *,
    market_date: object = MARKET_DATE,
) -> InvalidPaperTradeUpdateRawReceiptProjectionError:
    with pytest.raises(InvalidPaperTradeUpdateRawReceiptProjectionError) as captured:
        _ = project_paper_trade_update_receipts(
            reader,
            market_date=market_date,  # type: ignore[arg-type]
        )
    return captured.value


class _LegacyTradeUpdateReceiptsForbiddenReader(ExecutionStoreReader):
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path

    def trade_update_receipts(self) -> Never:
        raise AssertionError("legacy raw receipt read is forbidden")


class _FiveHundredVariableLimitReader(ExecutionStoreReader):
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path

    def _reader_connection(self) -> sqlite3.Connection:
        connection = super()._reader_connection()
        _ = connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 500)
        return connection


class _SnapshotReader(ExecutionStoreReader):
    __slots__ = ("_snapshot", "path")

    def __init__(self, path: Path, snapshot: object) -> None:
        self.path = path
        self._snapshot = snapshot

    def trade_update_receipt_projection_snapshot(
        self,
        *,
        market_date: dt.date,
    ) -> TradeUpdateReceiptProjectionSnapshot:
        return cast(TradeUpdateReceiptProjectionSnapshot, self._snapshot)
