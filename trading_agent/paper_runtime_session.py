from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol, final, override

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    open_alpaca_paper_order_stream,
)
from trading_agent.execution_ledger_reader import (
    ReconciliationLedger,
    trade_update_receipt_reasons,
)
from trading_agent.paper_execution_models import (
    PaperOrderIntent,
)
from trading_agent.paper_order_gate_models import (
    LatestCompletedBar,
    PaperOrderGateDecision,
)
from trading_agent.paper_portfolio_builder import build_paper_portfolio
from trading_agent.paper_protective_exit import missing_protective_oco_reasons
from trading_agent.paper_reconciliation import (
    PaperReconciliationSnapshot,
    reconcile_operational_paper_state,
)
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_runtime import (
    PaperHeartbeatStream,
    PaperRuntimeEpochChangedError,
    PaperRuntimeReadiness,
    PaperStateAndClockLoader,
    PaperStreamOpener,
    paper_runtime_receipt_reasons,
    read_paper_broker_state_and_clock,
)
from trading_agent.paper_runtime_admission import evaluate_runtime_order
from trading_agent.paper_runtime_safety import (
    daily_kill_latch_reasons,
    plan_runtime_safety,
)
from trading_agent.paper_safety_models import PaperSafetyPlanDecision

__all__ = (
    "InactivePaperRuntimeSessionError",
    "PaperOrderRuntimeSession",
    "PaperRuntimeReadiness",
    "open_paper_runtime_session",
    "probe_paper_runtime",
)


class PaperLedgerReader(Protocol):
    def reconciliation_ledger(self) -> ReconciliationLedger: ...


class InactivePaperRuntimeSessionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper 런타임 세션이 이미 종료되었습니다"


type PaperRuntimeProbeLoader = Callable[
    [AlpacaPaperCredentials, PaperLedgerReader],
    PaperRuntimeReadiness,
]


class PaperOrderRuntimeSession(Protocol):
    def readiness(self) -> PaperRuntimeReadiness: ...

    def evaluate_order(
        self,
        *,
        latest_bar: LatestCompletedBar,
        candidate_intent: PaperOrderIntent,
        liquidity_allowed_quantity: int,
        estimated_spread_bps: float,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperOrderGateDecision: ...


@final
class _LivePaperRuntimeSession:
    def __init__(
        self,
        credentials: AlpacaPaperCredentials,
        ledger_reader: PaperLedgerReader,
        state_loader: PaperStateAndClockLoader,
        stream: PaperHeartbeatStream,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._credentials = credentials
        self._ledger_reader = ledger_reader
        self._state_loader = state_loader
        self._stream = stream
        self._clock = clock
        self._active = True

    def readiness(self) -> PaperRuntimeReadiness:
        return self._collect_readiness(DEFAULT_PAPER_RISK_CONFIG)

    def _collect_readiness(
        self,
        config: PaperRiskConfig,
    ) -> PaperRuntimeReadiness:
        self._require_active()
        before_rest = self._stream.heartbeat(5.0)
        broker_state, market_clock = self._state_loader(self._credentials)
        ledger = self._ledger_reader.reconciliation_ledger()
        reconciliation = reconcile_operational_paper_state(
            PaperReconciliationSnapshot(
                account=broker_state.account,
                broker_orders=broker_state.open_orders,
                positions=broker_state.positions,
                stored_intents=ledger.intents,
                unresolved_intent_ids=ledger.unresolved_intent_ids,
                bound_account_fingerprint=ledger.account_fingerprint,
                order_states=ledger.order_states,
            )
        )
        portfolio = build_paper_portfolio(
            broker_state,
            ledger.intents,
            ledger.filled_intent_ids,
            config,
            order_states=ledger.order_states,
        )
        after_rest = self._stream.heartbeat(5.0)
        if before_rest.connection_epoch != after_rest.connection_epoch:
            raise PaperRuntimeEpochChangedError
        runtime_reasons = tuple(
            sorted(
                set(
                    (
                        *paper_runtime_receipt_reasons(
                            before_rest,
                            broker_state,
                            market_clock,
                            after_rest,
                        ),
                        *trade_update_receipt_reasons(ledger),
                        *daily_kill_latch_reasons(
                            ledger,
                            broker_state,
                            self._clock(),
                        ),
                    )
                )
            )
        )
        return PaperRuntimeReadiness(
            broker_state=broker_state,
            market_clock=market_clock,
            stream_heartbeat=after_rest,
            reconciliation=reconciliation,
            portfolio=portfolio,
            runtime_reasons=runtime_reasons,
            protective_exit_reasons=missing_protective_oco_reasons(
                portfolio,
                broker_state,
                ledger.protective_oco_plans,
            ),
        )

    def evaluate_order(
        self,
        *,
        latest_bar: LatestCompletedBar,
        candidate_intent: PaperOrderIntent,
        liquidity_allowed_quantity: int,
        estimated_spread_bps: float,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperOrderGateDecision:
        readiness = self._collect_readiness(config)
        return evaluate_runtime_order(
            readiness,
            latest_bar,
            candidate_intent,
            liquidity_allowed_quantity,
            estimated_spread_bps,
            config,
            self._clock(),
        )

    def plan_safety_actions(
        self,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperSafetyPlanDecision:
        return plan_runtime_safety(
            self._collect_readiness(config),
            self._clock(),
            config,
        )

    def _require_active(self) -> None:
        if not self._active:
            raise InactivePaperRuntimeSessionError

    def _close(self) -> None:
        self._active = False


@contextmanager
def _open_paper_runtime_session(
    credentials: AlpacaPaperCredentials,
    ledger_reader: PaperLedgerReader,
    *,
    state_loader: PaperStateAndClockLoader = read_paper_broker_state_and_clock,
    stream_opener: PaperStreamOpener = open_alpaca_paper_order_stream,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> Iterator[PaperOrderRuntimeSession]:
    with stream_opener(credentials) as stream:
        session = _LivePaperRuntimeSession(
            credentials,
            ledger_reader,
            state_loader,
            stream,
            _clock,
        )
        try:
            yield session
        finally:
            session._close()


@contextmanager
def open_paper_runtime_session(
    credentials: AlpacaPaperCredentials,
    ledger_reader: PaperLedgerReader,
) -> Iterator[PaperOrderRuntimeSession]:
    with _open_paper_runtime_session(
        credentials,
        ledger_reader,
        state_loader=read_paper_broker_state_and_clock,
        stream_opener=open_alpaca_paper_order_stream,
    ) as session:
        yield session


def probe_paper_runtime(
    credentials: AlpacaPaperCredentials,
    ledger_reader: PaperLedgerReader,
) -> PaperRuntimeReadiness:
    with open_paper_runtime_session(credentials, ledger_reader) as session:
        return session.readiness()


def _probe_paper_runtime(
    credentials: AlpacaPaperCredentials,
    ledger_reader: PaperLedgerReader,
    *,
    state_loader: PaperStateAndClockLoader,
    stream_opener: PaperStreamOpener,
) -> PaperRuntimeReadiness:
    with _open_paper_runtime_session(
        credentials,
        ledger_reader,
        state_loader=state_loader,
        stream_opener=stream_opener,
    ) as session:
        return session.readiness()
