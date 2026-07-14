from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import open_alpaca_paper_order_stream
from trading_agent.execution_errors import UnboundExecutionAccountError
from trading_agent.execution_ledger_reader import (
    ReconciliationLedger,
    trade_update_receipt_reasons,
)
from trading_agent.execution_store import ExecutionStore, ExecutionWriter
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_stream_recovery_runtime import (
    PaperRecoveryStateLoader,
    build_paper_stream_recovery_observation,
    read_paper_recovery_state,
)
from trading_agent.paper_trade_update_classification import (
    classify_committed_trade_update_receipt,
)
from trading_agent.paper_trade_update_ingestion import (
    PaperTradeUpdateIngestion,
    PaperTradeUpdateStream,
)
from trading_agent.trade_update_receipt_models import (
    InvalidTradeUpdateRawReceiptError,
)

type PaperTradeUpdateStreamOpener = Callable[
    [AlpacaPaperCredentials],
    AbstractContextManager[PaperTradeUpdateStream],
]


class PaperTradeUpdateRecoveryProbeError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper trade update REST 복구 증거가 생성되지 않았습니다"


@dataclass(frozen=True, slots=True)
class PaperTradeUpdateRecoveryProbe:
    completed_at: str
    recovery_order_count: int
    execution_detail_complete: bool
    blocking_reasons: tuple[str, ...] = ()


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
    state_loader: PaperRecoveryStateLoader,
    stream_opener: PaperTradeUpdateStreamOpener,
    _clock: Callable[[], dt.datetime],
) -> Iterator[PaperTradeUpdateIngestion]:
    with store.writer() as writer:
        _reprocess_pending_receipts(store, writer, _clock)
        with stream_opener(credentials) as stream:
            def recover() -> AccountFingerprint:
                before_rest = stream.heartbeat(5.0)
                if before_rest.connection_epoch != stream.connection_epoch:
                    raise PaperRuntimeEpochChangedError
                ledger = store.reconciliation_ledger()
                recovery_state = state_loader(
                    credentials,
                    ledger.unresolved_intent_ids,
                )
                after_rest = stream.heartbeat(5.0)
                recovery = build_paper_stream_recovery_observation(
                    before_rest,
                    after_rest,
                    recovery_state,
                    ledger,
                )
                _ = writer.bind_account(
                    recovery.account_fingerprint,
                    recovery_state.broker_state.account.observed_at,
                )
                _ = writer.append_paper_stream_recovery(recovery)
                return recovery.account_fingerprint

            fingerprint = recover()

            def recover_after_quarantine() -> None:
                _ = recover()

            ingestion = PaperTradeUpdateIngestion(
                stream,
                writer,
                fingerprint,
                _clock,
                recover_after_quarantine,
            )
            try:
                yield ingestion
            finally:
                ingestion._close()


def _reprocess_pending_receipts(
    store: ExecutionStore,
    writer: ExecutionWriter,
    clock: Callable[[], dt.datetime],
) -> None:
    ledger = store.reconciliation_ledger()
    pending_keys = ledger.pending_trade_update_receipt_keys
    if not pending_keys:
        return
    fingerprint = ledger.account_fingerprint
    if fingerprint is None:
        raise UnboundExecutionAccountError
    pending = tuple(
        receipt
        for receipt in store.trade_update_receipts()
        if receipt.receipt_key in pending_keys
    )
    if frozenset(receipt.receipt_key for receipt in pending) != pending_keys:
        raise InvalidTradeUpdateRawReceiptError
    classified_at = clock()
    for receipt in pending:
        _ = classify_committed_trade_update_receipt(
            writer,
            receipt,
            account_fingerprint=fingerprint,
            classified_at=classified_at,
        )


@contextmanager
def open_paper_trade_update_ingestion(
    credentials: AlpacaPaperCredentials,
    store: ExecutionStore,
) -> Iterator[PaperTradeUpdateIngestion]:
    with _open_paper_trade_update_ingestion(
        credentials,
        store,
        state_loader=read_paper_recovery_state,
        stream_opener=_open_production_trade_update_stream,
        _clock=lambda: dt.datetime.now(dt.UTC),
    ) as ingestion:
        yield ingestion


def probe_paper_trade_update_recovery(
    credentials: AlpacaPaperCredentials,
    store: ExecutionStore,
) -> PaperTradeUpdateRecoveryProbe:
    before_count = len(store.paper_stream_recoveries())
    with open_paper_trade_update_ingestion(credentials, store):
        pass
    recoveries = store.paper_stream_recoveries()
    if len(recoveries) <= before_count:
        raise PaperTradeUpdateRecoveryProbeError
    latest = recoveries[-1]
    ledger = store.reconciliation_ledger()
    order_count = sum(
        order.recovery_id == latest.recovery_id
        for order in store.paper_recovery_orders()
    )
    return PaperTradeUpdateRecoveryProbe(
        latest.completed_at,
        order_count,
        latest.execution_detail_complete
        and all(state.execution_detail_complete for state in ledger.order_states),
        _recovery_blocking_reasons(ledger),
    )


def _recovery_blocking_reasons(
    ledger: ReconciliationLedger,
) -> tuple[str, ...]:
    reasons = [*trade_update_receipt_reasons(ledger)]
    for state in ledger.order_states:
        reasons.extend(state.anomaly_reasons)
    return tuple(sorted(set(reasons)))
