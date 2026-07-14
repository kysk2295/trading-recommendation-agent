from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.paper_trade_update_ingestion_fixtures import (
    TradeUpdateStream,
    broker_state,
    recovery_state,
    state_loader,
)
from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    OTHER_FINGERPRINT,
    intent,
    trade_update,
)
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperStreamEpoch,
    PaperTradeUpdateFrame,
    PaperTradeUpdateWireKind,
)
from trading_agent.execution_errors import AccountBindingConflictError
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_stream_recovery_runtime import (
    PaperRecoveryState,
    PaperStreamRecoveryIncompleteError,
)
from trading_agent.paper_trade_update_ingestion import (
    InactivePaperTradeUpdateIngestionError,
    PaperTradeUpdateIngestionState,
)
from trading_agent.paper_trade_update_runtime import _open_paper_trade_update_ingestion
from trading_agent.trade_update_receipts import TradeUpdateReceiptReason


def test_malformed_binary_frame_is_quarantined_without_losing_raw_bytes(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.save_intent(intent(), quantity=100)
    raw = b"\xff\x00malformed-paper-frame"
    stream = TradeUpdateStream(PaperTradeUpdateFrame(raw, PaperTradeUpdateWireKind.BINARY))

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
    assert result.reason is TradeUpdateReceiptReason.PROTOCOL_ERROR
    assert store.trade_update_receipts()[0].raw_payload == raw
    assert store.trade_updates(intent().intent_id) == ()
    assert stream.heartbeat_count == 4
    assert len(store.paper_stream_recoveries()) == 2
    assert store.reconciliation_ledger().unrecovered_trade_update_quarantine_keys == frozenset()


def test_semantically_unmatched_frame_is_quarantined_after_raw_commit(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    raw_object = json.loads(trade_update().payload_json)
    raw_object["data"]["order"]["client_order_id"] = "unknown-intent"
    raw = json.dumps(raw_object, separators=(",", ":")).encode()
    stream = TradeUpdateStream(PaperTradeUpdateFrame(raw, PaperTradeUpdateWireKind.BINARY))

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
    assert result.reason is TradeUpdateReceiptReason.UNKNOWN_INTENT
    assert store.trade_update_receipts()[0].raw_payload == raw
    assert store.trade_update_receipt_dispositions()[0].reason is result.reason
    assert store.account_fingerprint() == FINGERPRINT
    assert stream.heartbeat_count == 4


def test_ingestion_rejects_a_rest_account_different_from_the_ledger(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.bind_account(OTHER_FINGERPRINT, OBSERVED_AT)
        _ = writer.save_intent(intent(), quantity=100)

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield TradeUpdateStream()

    with (
        pytest.raises(AccountBindingConflictError, match="다른 Alpaca paper 계좌"),
        _open_paper_trade_update_ingestion(
            AlpacaPaperCredentials("test-key", "test-secret"),
            store,
            state_loader=lambda _, ledger: recovery_state(ledger.unresolved_intent_ids),
            stream_opener=stream_opener,
            _clock=lambda: OBSERVED_AT,
        ),
    ):
        pytest.fail("다른 계좌로 ingestion이 열리면 안 됩니다")


def test_ingestion_does_not_record_recovery_across_connection_epochs(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    stream = TradeUpdateStream(epochs=(PaperStreamEpoch("epoch-1"), PaperStreamEpoch("epoch-2")))

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    with (
        pytest.raises(PaperRuntimeEpochChangedError, match="연결 세대"),
        _open_paper_trade_update_ingestion(
            AlpacaPaperCredentials("test-key", "test-secret"),
            store,
            state_loader=lambda _, ledger: recovery_state(ledger.unresolved_intent_ids),
            stream_opener=stream_opener,
            _clock=lambda: OBSERVED_AT,
        ),
    ):
        pytest.fail("두 connection epoch에 걸친 복구가 완료되면 안 됩니다")

    assert store.paper_stream_recoveries() == ()
    assert stream.receive_count == 0


def test_ingestion_requires_every_unresolved_intent_in_rest_recovery(
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

    with (
        pytest.raises(PaperStreamRecoveryIncompleteError, match="미해결"),
        _open_paper_trade_update_ingestion(
            AlpacaPaperCredentials("test-key", "test-secret"),
            store,
            state_loader=lambda _, __: PaperRecoveryState(broker_state(), ()),
            stream_opener=stream_opener,
            _clock=lambda: OBSERVED_AT,
        ),
    ):
        pytest.fail("미해결 intent 조회 없이 ingestion이 열리면 안 됩니다")

    assert store.paper_stream_recoveries() == ()
    assert stream.receive_count == 0


def test_failed_post_quarantine_recovery_closes_ingestion_and_stays_blocked(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    stream = TradeUpdateStream(PaperTradeUpdateFrame(b"malformed", PaperTradeUpdateWireKind.BINARY))
    load_count = 0

    def load_state(
        _: AlpacaPaperCredentials,
        ledger: ReconciliationLedger,
    ) -> PaperRecoveryState:
        nonlocal load_count
        load_count += 1
        if load_count == 2:
            raise PaperStreamRecoveryIncompleteError(("forced recovery failure",))
        observed_at = OBSERVED_AT + dt.timedelta(seconds=stream.heartbeat_count - 1.5)
        return recovery_state(ledger.unresolved_intent_ids, observed_at)

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    with _open_paper_trade_update_ingestion(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        state_loader=load_state,
        stream_opener=stream_opener,
        _clock=lambda: OBSERVED_AT,
    ) as ingestion:
        with pytest.raises(PaperStreamRecoveryIncompleteError, match="forced"):
            _ = ingestion.ingest_next(1.0)
        with pytest.raises(InactivePaperTradeUpdateIngestionError, match="종료"):
            _ = ingestion.ingest_next(1.0)

    ledger = store.reconciliation_ledger()
    assert ledger.unrecovered_trade_update_quarantine_keys
    assert len(store.paper_stream_recoveries()) == 1
