from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_errors import UnboundExecutionAccountError
from trading_agent.execution_store import (
    ExecutionLedgerGeneration,
    ExecutionStore,
    ExecutionWriter,
)
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoverySnapshot,
)
from trading_agent.paper_runtime import PaperRuntimeEpochChangedError
from trading_agent.paper_stream_recovery_runtime import (
    PaperRecoveryStateLoader,
    build_paper_stream_recovery_observation,
)
from trading_agent.paper_trade_update_classification import classify_committed_trade_update_receipt
from trading_agent.paper_trade_update_ingestion import (
    PaperTradeUpdateIngestion,
    PaperTradeUpdateStream,
)
from trading_agent.trade_update_receipt_models import InvalidTradeUpdateRawReceiptError

type PaperTradeUpdateStreamOpener = Callable[
    [AlpacaPaperCredentials],
    AbstractContextManager[PaperTradeUpdateStream],
]


@dataclass(frozen=True, slots=True)
class PaperStreamOwnerDependencies:
    state_loader: PaperRecoveryStateLoader
    stream_opener: PaperTradeUpdateStreamOpener
    clock: Callable[[], dt.datetime]


@dataclass(frozen=True, slots=True)
class PaperRecoveryCheckpoint:
    account_fingerprint: AccountFingerprint
    connection_epoch: str
    ledger_generation: ExecutionLedgerGeneration
    mutation_recovery: PaperMutationRecoverySnapshot


@dataclass(frozen=True, slots=True)
class PaperStreamOwner:
    ingestion: PaperTradeUpdateIngestion
    stream: PaperTradeUpdateStream
    writer: ExecutionWriter
    recovery: Callable[[], PaperRecoveryCheckpoint]
    store: ExecutionStore


@contextmanager
def open_paper_stream_owner(
    credentials: AlpacaPaperCredentials,
    store: ExecutionStore,
    dependencies: PaperStreamOwnerDependencies,
) -> Iterator[PaperStreamOwner]:
    with store.writer() as writer:
        _reprocess_pending_receipts(store, writer, dependencies.clock)
        with dependencies.stream_opener(credentials) as stream:

            def recover() -> PaperRecoveryCheckpoint:
                before_rest = stream.heartbeat(5.0)
                if before_rest.connection_epoch != stream.connection_epoch:
                    raise PaperRuntimeEpochChangedError
                ledger = store.reconciliation_ledger()
                recovery_state = dependencies.state_loader(
                    credentials,
                    ledger,
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
                return PaperRecoveryCheckpoint(
                    recovery.account_fingerprint,
                    recovery.connection_epoch,
                    writer.ledger_generation(),
                    PaperMutationRecoverySnapshot(
                        recovery.connection_epoch,
                        recovery.started_at,
                        recovery.completed_at,
                        recovery_state,
                    ),
                )

            checkpoint = recover()

            def recover_after_quarantine() -> None:
                _ = recover()

            ingestion = PaperTradeUpdateIngestion(
                stream,
                writer,
                checkpoint.account_fingerprint,
                dependencies.clock,
                recover_after_quarantine,
            )
            try:
                yield PaperStreamOwner(ingestion, stream, writer, recover, store)
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
    pending = tuple(receipt for receipt in store.trade_update_receipts() if receipt.receipt_key in pending_keys)
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
