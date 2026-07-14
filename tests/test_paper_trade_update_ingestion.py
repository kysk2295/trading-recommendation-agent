from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    OTHER_FINGERPRINT,
    intent,
    trade_update,
)
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import PaperStreamEpoch
from trading_agent.alpaca_trade_updates import AlpacaTradeUpdate
from trading_agent.execution_errors import AccountBindingConflictError
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import (
    PaperAccountSnapshot,
    PaperBrokerState,
)
from trading_agent.paper_trade_update_ingestion import (
    InactivePaperTradeUpdateIngestionError,
    _open_paper_trade_update_ingestion,
)


class _TradeUpdateStream:
    connection_epoch = PaperStreamEpoch("epoch-from-stream")

    def __init__(self) -> None:
        self.receive_count = 0

    def receive_trade_update(self, timeout_seconds: float) -> AlpacaTradeUpdate:
        assert timeout_seconds == 1.0
        self.receive_count += 1
        return trade_update()


def _broker_state() -> PaperBrokerState:
    return PaperBrokerState(
        PaperAccountSnapshot(
            observed_at=OBSERVED_AT,
            status="ACTIVE",
            trading_blocked=False,
            equity=Decimal(30_000),
            last_equity=Decimal(30_000),
            buying_power=Decimal(60_000),
            account_fingerprint=FINGERPRINT,
        ),
        (),
        (),
    )


def test_ingestion_binds_rest_account_and_persists_stream_epoch(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.save_intent(intent(), quantity=100)
    stream = _TradeUpdateStream()

    @contextmanager
    def stream_opener(
        credentials: AlpacaPaperCredentials,
    ) -> Iterator[_TradeUpdateStream]:
        assert credentials.key_id == "test-key"
        yield stream

    with _open_paper_trade_update_ingestion(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        state_loader=lambda _: _broker_state(),
        stream_opener=stream_opener,
        _clock=lambda: OBSERVED_AT + dt.timedelta(seconds=1),
    ) as ingestion:
        first = ingestion.ingest_next(1.0)
        replay = ingestion.ingest_next(1.0)

    stored = store.trade_updates(intent().intent_id)
    assert first is True
    assert replay is False
    assert stream.receive_count == 2
    assert store.account_fingerprint() == FINGERPRINT
    assert stored[0].connection_epoch == "epoch-from-stream"
    assert stored[0].received_at == "2026-07-14T13:36:03+00:00"
    with pytest.raises(InactivePaperTradeUpdateIngestionError, match="종료"):
        _ = ingestion.ingest_next(1.0)
    assert stream.receive_count == 2


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
    ) -> Iterator[_TradeUpdateStream]:
        yield _TradeUpdateStream()

    with (
        pytest.raises(AccountBindingConflictError, match="다른 Alpaca paper 계좌"),
        _open_paper_trade_update_ingestion(
            AlpacaPaperCredentials("test-key", "test-secret"),
            store,
            state_loader=lambda _: _broker_state(),
            stream_opener=stream_opener,
            _clock=lambda: OBSERVED_AT,
        ),
    ):
        pytest.fail("다른 계좌로 ingestion이 열리면 안 됩니다")
