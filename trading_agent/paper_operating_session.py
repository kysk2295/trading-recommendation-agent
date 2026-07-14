from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Final, final

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import open_alpaca_paper_order_stream
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_operating_session_models import (
    BusyPaperOperatingSessionError,
    InactivePaperOperatingSessionError,
    PaperOperatingSession,
    PaperOrderAdmissionRequest,
)
from trading_agent.paper_order_gate_models import (
    BlockedPaperOrderGateDecision,
    PaperOrderGateDecision,
    PaperOrderGateState,
)
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_runtime import PaperStateAndClockLoader, read_paper_broker_state_and_clock
from trading_agent.paper_runtime_session import _LivePaperRuntimeSession
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperSafetyPhase,
    PaperSafetyPlan,
    PaperSafetyPlanDecision,
)
from trading_agent.paper_stream_owner import (
    PaperRecoveryCheckpoint,
    PaperStreamOwner,
    PaperStreamOwnerDependencies,
    open_paper_stream_owner,
)
from trading_agent.paper_stream_recovery_runtime import read_paper_recovery_state
from trading_agent.paper_trade_update_classification import PaperTradeUpdateIngestionResult

LEDGER_GENERATION_CHANGED: Final = "current-epoch REST 복구 뒤 실행 원장 세대가 변경됐습니다"
STREAM_EPOCH_CHANGED: Final = "current-epoch REST 복구 뒤 주문 스트림 연결 세대가 변경됐습니다"


@dataclass(frozen=True, slots=True)
class PaperOperatingSessionDependencies:
    owner: PaperStreamOwnerDependencies
    runtime_state_loader: PaperStateAndClockLoader
    clock: Callable[[], dt.datetime]


@final
class _LivePaperOperatingSession:
    def __init__(
        self,
        owner: PaperStreamOwner,
        runtime: _LivePaperRuntimeSession,
    ) -> None:
        self._owner = owner
        self._runtime = runtime
        self._operation_lock = Lock()
        self._active = True

    def ingest_next(self, timeout_seconds: float) -> PaperTradeUpdateIngestionResult:
        with self._exclusive_operation():
            return self._owner.ingestion.ingest_next(timeout_seconds)

    def evaluate_order(
        self,
        request: PaperOrderAdmissionRequest,
    ) -> PaperOrderGateDecision:
        with self._exclusive_operation():
            checkpoint = self._owner.recovery()
            barrier_reasons = self._barrier_reasons(checkpoint)
            if barrier_reasons:
                return _blocked_barrier(barrier_reasons)
            decision = self._runtime.evaluate_order(
                latest_bar=request.latest_bar,
                candidate_intent=request.candidate_intent,
                liquidity_allowed_quantity=request.liquidity_allowed_quantity,
                estimated_spread_bps=request.estimated_spread_bps,
                config=request.config,
            )
            barrier_reasons = self._barrier_reasons(checkpoint)
            return decision if not barrier_reasons else _blocked_barrier(barrier_reasons)

    def plan_safety_actions(
        self,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperSafetyPlanDecision:
        with self._exclusive_operation():
            checkpoint = self._owner.recovery()
            barrier_reasons = self._barrier_reasons(checkpoint)
            if barrier_reasons:
                return BlockedPaperSafetyPlan(barrier_reasons)
            decision = self._runtime.plan_safety_actions(config)
            barrier_reasons = self._barrier_reasons(checkpoint)
            if barrier_reasons:
                return BlockedPaperSafetyPlan(barrier_reasons)
            if isinstance(decision, PaperSafetyPlan) and decision.phase is not PaperSafetyPhase.MONITORING:
                _ = self._owner.writer.save_paper_safety_plan(decision)
            return decision

    def _barrier_reasons(
        self,
        checkpoint: PaperRecoveryCheckpoint,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if checkpoint.connection_epoch != self._owner.stream.connection_epoch:
            reasons.append(STREAM_EPOCH_CHANGED)
        if checkpoint.ledger_generation != self._owner.writer.ledger_generation():
            reasons.append(LEDGER_GENERATION_CHANGED)
        return tuple(reasons)

    @contextmanager
    def _exclusive_operation(self) -> Iterator[None]:
        self._require_active()
        if not self._operation_lock.acquire(blocking=False):
            raise BusyPaperOperatingSessionError
        try:
            yield
        finally:
            self._operation_lock.release()

    def _require_active(self) -> None:
        if not self._active:
            raise InactivePaperOperatingSessionError

    def _close(self) -> None:
        self._active = False


@contextmanager
def _open_paper_operating_session(
    credentials: AlpacaPaperCredentials,
    store: ExecutionStore,
    dependencies: PaperOperatingSessionDependencies,
) -> Iterator[PaperOperatingSession]:
    with open_paper_stream_owner(credentials, store, dependencies.owner) as owner:
        runtime = _LivePaperRuntimeSession(
            credentials,
            store,
            dependencies.runtime_state_loader,
            owner.stream,
            dependencies.clock,
        )
        session = _LivePaperOperatingSession(owner, runtime)
        try:
            yield session
        finally:
            session._close()
            runtime._close()


@contextmanager
def open_paper_operating_session(
    credentials: AlpacaPaperCredentials,
    store: ExecutionStore,
) -> Iterator[PaperOperatingSession]:
    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(
            read_paper_recovery_state,
            open_alpaca_paper_order_stream,
            _utc_now,
        ),
        read_paper_broker_state_and_clock,
        _utc_now,
    )
    with _open_paper_operating_session(credentials, store, dependencies) as session:
        yield session


def _blocked_barrier(reasons: tuple[str, ...]) -> BlockedPaperOrderGateDecision:
    return BlockedPaperOrderGateDecision(
        PaperOrderGateState.RECONCILIATION_BLOCKED,
        reasons,
    )


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
