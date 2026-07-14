from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol, final, override

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperStreamEpoch,
    open_alpaca_paper_order_stream,
)
from trading_agent.alpaca_trade_updates import AlpacaTradeUpdate
from trading_agent.execution_store import ExecutionStore, ExecutionWriter
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_runtime import PaperStateLoader, read_paper_broker_state


class InactivePaperTradeUpdateIngestionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper trade update ingestion 구간이 종료되었습니다"


class PaperTradeUpdateStream(Protocol):
    @property
    def connection_epoch(self) -> PaperStreamEpoch: ...

    def receive_trade_update(self, timeout_seconds: float) -> AlpacaTradeUpdate: ...


type PaperTradeUpdateStreamOpener = Callable[
    [AlpacaPaperCredentials],
    AbstractContextManager[PaperTradeUpdateStream],
]


@final
class PaperTradeUpdateIngestion:
    __slots__ = (
        "_account_fingerprint",
        "_active",
        "_clock",
        "_stream",
        "_writer",
    )

    def __init__(
        self,
        stream: PaperTradeUpdateStream,
        writer: ExecutionWriter,
        account_fingerprint: AccountFingerprint,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._stream = stream
        self._writer = writer
        self._account_fingerprint = account_fingerprint
        self._clock = clock
        self._active = True

    def ingest_next(self, timeout_seconds: float) -> bool:
        self._require_active()
        update = self._stream.receive_trade_update(timeout_seconds)
        return self._writer.append_trade_update(
            update,
            account_fingerprint=self._account_fingerprint,
            connection_epoch=self._stream.connection_epoch,
            received_at=self._clock(),
        )

    def _require_active(self) -> None:
        if not self._active:
            raise InactivePaperTradeUpdateIngestionError

    def _close(self) -> None:
        self._active = False


@contextmanager
def _open_production_trade_update_stream(
    credentials: AlpacaPaperCredentials,
) -> Iterator[PaperTradeUpdateStream]:
    with open_alpaca_paper_order_stream(credentials) as stream:
        yield stream


@contextmanager
def _open_paper_trade_update_ingestion(
    credentials: AlpacaPaperCredentials,
    store: ExecutionStore,
    *,
    state_loader: PaperStateLoader,
    stream_opener: PaperTradeUpdateStreamOpener,
    _clock: Callable[[], dt.datetime],
) -> Iterator[PaperTradeUpdateIngestion]:
    with stream_opener(credentials) as stream, store.writer() as writer:
        broker_state = state_loader(credentials)
        fingerprint = broker_state.account.account_fingerprint
        _ = writer.bind_account(fingerprint, broker_state.account.observed_at)
        ingestion = PaperTradeUpdateIngestion(stream, writer, fingerprint, _clock)
        try:
            yield ingestion
        finally:
            ingestion._close()


@contextmanager
def open_paper_trade_update_ingestion(
    credentials: AlpacaPaperCredentials,
    store: ExecutionStore,
) -> Iterator[PaperTradeUpdateIngestion]:
    with _open_paper_trade_update_ingestion(
        credentials,
        store,
        state_loader=read_paper_broker_state,
        stream_opener=_open_production_trade_update_stream,
        _clock=lambda: dt.datetime.now(dt.UTC),
    ) as ingestion:
        yield ingestion
