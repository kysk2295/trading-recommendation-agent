from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import open_alpaca_paper_order_stream
from trading_agent.execution_ledger_reader import (
    ReconciliationLedger,
    trade_update_receipt_reasons,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_operating_session import (
    PaperOperatingSessionDependencies as PaperOperatingSessionDependencies,
)
from trading_agent.paper_operating_session import (
    _open_paper_operating_session as _open_paper_operating_session,
)
from trading_agent.paper_operating_session import (
    open_paper_operating_session as open_paper_operating_session,
)
from trading_agent.paper_operating_session_models import (
    PaperOperatingSession as PaperOperatingSession,
)
from trading_agent.paper_operating_session_models import (
    PaperOrderAdmissionRequest as PaperOrderAdmissionRequest,
)
from trading_agent.paper_stream_owner import (
    PaperStreamOwnerDependencies,
    PaperTradeUpdateStreamOpener,
    open_paper_stream_owner,
)
from trading_agent.paper_stream_recovery_runtime import PaperRecoveryStateLoader, read_paper_recovery_state
from trading_agent.paper_trade_update_ingestion import (
    PaperTradeUpdateIngestion,
    PaperTradeUpdateStream,
)

__all__ = (
    "PaperOperatingSession",
    "PaperOrderAdmissionRequest",
    "open_paper_operating_session",
    "open_paper_trade_update_ingestion",
    "probe_paper_trade_update_recovery",
)


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
    dependencies = PaperStreamOwnerDependencies(state_loader, stream_opener, _clock)
    with open_paper_stream_owner(credentials, store, dependencies) as owner:
        yield owner.ingestion


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
    order_count = sum(order.recovery_id == latest.recovery_id for order in store.paper_recovery_orders())
    return PaperTradeUpdateRecoveryProbe(
        latest.completed_at,
        order_count,
        latest.execution_detail_complete and all(state.execution_detail_complete for state in ledger.order_states),
        _recovery_blocking_reasons(ledger),
    )


def _recovery_blocking_reasons(
    ledger: ReconciliationLedger,
) -> tuple[str, ...]:
    reasons = [*trade_update_receipt_reasons(ledger)]
    for state in ledger.order_states:
        reasons.extend(state.anomaly_reasons)
    return tuple(sorted(set(reasons)))
