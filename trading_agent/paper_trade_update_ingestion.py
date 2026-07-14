from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Protocol, final, override

from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
    PaperTradeUpdateFrame,
)
from trading_agent.execution_store import ExecutionWriter
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_trade_update_classification import (
    PaperTradeUpdateIngestionResult,
    PaperTradeUpdateIngestionState,
    classify_committed_trade_update_receipt,
)

_FAIL_STOP_ERRORS = (Exception,)


class InactivePaperTradeUpdateIngestionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper trade update ingestion 구간이 종료되었습니다"


class PaperTradeUpdateStream(Protocol):
    @property
    def connection_epoch(self) -> PaperStreamEpoch: ...

    def receive_trade_update_frame(
        self,
        timeout_seconds: float,
    ) -> PaperTradeUpdateFrame: ...

    def heartbeat(self, timeout_seconds: float) -> PaperOrderStreamHeartbeat: ...


@final
class PaperTradeUpdateIngestion:
    __slots__ = (
        "_account_fingerprint",
        "_active",
        "_clock",
        "_recover",
        "_stream",
        "_writer",
    )

    def __init__(
        self,
        stream: PaperTradeUpdateStream,
        writer: ExecutionWriter,
        account_fingerprint: AccountFingerprint,
        clock: Callable[[], dt.datetime],
        recover: Callable[[], None],
    ) -> None:
        self._stream = stream
        self._writer = writer
        self._account_fingerprint = account_fingerprint
        self._clock = clock
        self._recover = recover
        self._active = True

    def ingest_next(
        self,
        timeout_seconds: float,
    ) -> PaperTradeUpdateIngestionResult:
        self._require_active()
        try:
            frame = self._stream.receive_trade_update_frame(timeout_seconds)
        except TimeoutError:
            raise
        except _FAIL_STOP_ERRORS:
            self._close()
            raise
        try:
            received_at = self._clock()
            receipt = self._writer.save_trade_update_receipt(
                frame,
                account_fingerprint=self._account_fingerprint,
                connection_epoch=self._stream.connection_epoch,
                received_at=received_at,
            )
            result = classify_committed_trade_update_receipt(
                self._writer,
                receipt,
                account_fingerprint=self._account_fingerprint,
                classified_at=received_at,
            )
            if result.state is PaperTradeUpdateIngestionState.QUARANTINED:
                self._recover()
            return result
        except _FAIL_STOP_ERRORS:
            self._close()
            raise

    def _require_active(self) -> None:
        if not self._active:
            raise InactivePaperTradeUpdateIngestionError

    def _close(self) -> None:
        self._active = False
