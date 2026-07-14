from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.paper_trade_update_ingestion_fixtures import (
    TradeUpdateStream,
    recovery_state,
    state_loader,
)
from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    intent,
    trade_update,
)
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperTradeUpdateFrame,
    PaperTradeUpdateWireKind,
)
from trading_agent.execution_store import ExecutionStore, WriterLeaseUnavailableError
from trading_agent.paper_trade_update_ingestion import (
    InactivePaperTradeUpdateIngestionError,
    PaperTradeUpdateIngestionState,
)
from trading_agent.paper_trade_update_runtime import _open_paper_trade_update_ingestion


def test_ingestion_binds_rest_account_and_persists_stream_epoch(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.save_intent(intent(), quantity=100)
    stream = TradeUpdateStream()

    @contextmanager
    def stream_opener(
        credentials: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        assert credentials.key_id == "test-key"
        yield stream

    with _open_paper_trade_update_ingestion(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        state_loader=state_loader(stream),
        stream_opener=stream_opener,
        _clock=lambda: OBSERVED_AT + dt.timedelta(seconds=1),
    ) as ingestion:
        first = ingestion.ingest_next(1.0)
        replay = ingestion.ingest_next(1.0)

    stored = store.trade_updates(intent().intent_id)
    assert first.state is PaperTradeUpdateIngestionState.ACCEPTED
    assert first.event_inserted is True
    assert replay.state is PaperTradeUpdateIngestionState.ACCEPTED
    assert replay.event_inserted is False
    assert stream.receive_count == 2
    assert store.account_fingerprint() == FINGERPRINT
    assert stored[0].connection_epoch == "epoch-from-stream"
    assert stored[0].received_at == "2026-07-14T13:36:03+00:00"
    assert len(store.trade_update_receipt_dispositions()) == 1
    assert store.pending_trade_update_receipt_keys() == frozenset()
    assert len(store.paper_stream_recoveries()) == 1
    assert len(store.paper_recovery_orders()) == 1
    assert stream.heartbeat_count == 2
    with pytest.raises(InactivePaperTradeUpdateIngestionError, match="종료"):
        _ = ingestion.ingest_next(1.0)
    assert stream.receive_count == 2


def test_ingestion_acquires_the_single_writer_before_opening_the_stream(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    stream_entered = False

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        nonlocal stream_entered
        stream_entered = True
        yield TradeUpdateStream()

    with (
        store.writer(),
        pytest.raises(WriterLeaseUnavailableError, match="이미 실행"),
        _open_paper_trade_update_ingestion(
            AlpacaPaperCredentials("test-key", "test-secret"),
            store,
            state_loader=lambda _, ledger: recovery_state(ledger.unresolved_intent_ids),
            stream_opener=stream_opener,
            _clock=lambda: OBSERVED_AT,
        ),
    ):
        pytest.fail("두 번째 Writer가 열린 뒤 stream을 열면 안 됩니다")

    assert stream_entered is False


def test_startup_reprocesses_a_committed_pending_raw_receipt(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    raw = trade_update().payload_json.encode()
    with store.writer() as writer:
        _ = writer.bind_account(FINGERPRINT, OBSERVED_AT)
        _ = writer.save_intent(intent(), quantity=100)
        receipt = writer.save_trade_update_receipt(
            PaperTradeUpdateFrame(raw, PaperTradeUpdateWireKind.BINARY),
            account_fingerprint=FINGERPRINT,
            connection_epoch="crashed-epoch",
            received_at=OBSERVED_AT - dt.timedelta(seconds=1),
        )
    stream = TradeUpdateStream()

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    with _open_paper_trade_update_ingestion(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        state_loader=state_loader(stream),
        stream_opener=stream_opener,
        _clock=lambda: OBSERVED_AT,
    ):
        pass

    stored = store.trade_updates(intent().intent_id)
    disposition = store.trade_update_receipt_dispositions()[0]
    assert disposition.receipt_key == receipt.receipt_key
    assert disposition.reason is None
    assert stored[0].connection_epoch == "crashed-epoch"
    assert stored[0].received_at == "2026-07-14T13:36:01+00:00"
    assert store.pending_trade_update_receipt_keys() == frozenset()
    assert len(store.paper_stream_recoveries()) == 1


@pytest.mark.parametrize(
    "wire_kind",
    (PaperTradeUpdateWireKind.TEXT, PaperTradeUpdateWireKind.BINARY),
)
def test_empty_frame_is_preserved_then_quarantined(
    tmp_path: Path,
    wire_kind: PaperTradeUpdateWireKind,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    stream = TradeUpdateStream(PaperTradeUpdateFrame(b"", wire_kind))

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    with _open_paper_trade_update_ingestion(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        state_loader=state_loader(stream),
        stream_opener=stream_opener,
        _clock=lambda: OBSERVED_AT,
    ) as ingestion:
        result = ingestion.ingest_next(1.0)

    assert result.state is PaperTradeUpdateIngestionState.QUARANTINED
    assert store.trade_update_receipts()[0].raw_payload == b""
    assert store.pending_trade_update_receipt_keys() == frozenset()


def test_disposition_storage_failure_closes_ingestion_and_leaves_pending_receipt(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.save_intent(intent(), quantity=100)
    stream = TradeUpdateStream()

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    with _open_paper_trade_update_ingestion(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        state_loader=state_loader(stream),
        stream_opener=stream_opener,
        _clock=lambda: OBSERVED_AT,
    ) as ingestion:
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "CREATE TRIGGER abort_disposition_insert "
                "BEFORE INSERT ON trade_update_receipt_dispositions "
                "BEGIN SELECT RAISE(ABORT, 'forced-disposition-failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="forced-disposition"):
            _ = ingestion.ingest_next(1.0)
        with pytest.raises(InactivePaperTradeUpdateIngestionError, match="종료"):
            _ = ingestion.ingest_next(1.0)

    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER abort_disposition_insert")
    assert store.pending_trade_update_receipt_keys()
