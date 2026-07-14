from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import final

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_mutation_runtime import PaperMutationBrokerOpener
from trading_agent.paper_current_protective_exit import plan_current_protective_exit
from trading_agent.paper_execution_models import IntentId
from trading_agent.paper_mutation_executor import (
    PaperMutationExecutor,
    PaperMutationExecutorDependencies,
)
from trading_agent.paper_mutation_recovery import (
    PaperMutationRecovery,
    PaperMutationRecoveryDependencies,
)
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoveryResult,
    PaperMutationRecoveryState,
)
from trading_agent.paper_operating_mutation_models import (
    PaperEntryMutationExecution,
    PaperProtectiveMutationExecution,
    PaperSafetyMutationExecution,
)
from trading_agent.paper_operating_session_models import PaperMutationRecoveryBarrierError, PaperOrderAdmissionRequest
from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    BlockedPaperOrderGateDecision,
    PaperOrderGateState,
)
from trading_agent.paper_protective_exit import (
    BlockedProtectiveExitPlan,
    NoProtectiveExitRequired,
)
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_runtime_session import _LivePaperRuntimeSession
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperSafetyPhase,
)
from trading_agent.paper_safety_mutation_guard import (
    repeated_acknowledged_safety_action_reasons,
)
from trading_agent.paper_stream_owner import PaperRecoveryCheckpoint, PaperStreamOwner

type PaperCheckpointBarrier = Callable[[PaperRecoveryCheckpoint], tuple[str, ...]]


@final
class PaperOperatingMutationExecution:
    def __init__(
        self,
        owner: PaperStreamOwner,
        runtime: _LivePaperRuntimeSession,
        credentials: AlpacaPaperCredentials,
        broker_opener: PaperMutationBrokerOpener,
        clock: Callable[[], dt.datetime],
        barrier: PaperCheckpointBarrier,
        stream_epoch_changed_reason: str,
    ) -> None:
        self._owner = owner
        self._runtime = runtime
        self._credentials = credentials
        self._broker_opener = broker_opener
        self._clock = clock
        self._barrier = barrier
        self._stream_epoch_changed_reason = stream_epoch_changed_reason

    def recover(self) -> tuple[PaperMutationRecoveryResult, ...]:
        checkpoint = self._owner.recovery()
        reasons = self._barrier(checkpoint)
        if reasons:
            raise PaperMutationRecoveryBarrierError(reasons)
        return self._recover(checkpoint)

    def _recover(
        self,
        checkpoint: PaperRecoveryCheckpoint,
    ) -> tuple[PaperMutationRecoveryResult, ...]:
        return PaperMutationRecovery(
            PaperMutationRecoveryDependencies(
                self._owner.writer,
                self._owner.store.intents,
                self._owner.store.paper_mutation_intents,
                self._owner.store.paper_mutation_events,
                self._owner.store.protective_oco_plans,
            )
        ).recover(checkpoint.mutation_recovery)

    def _checkpoint_for_execution(
        self,
    ) -> tuple[PaperRecoveryCheckpoint, tuple[str, ...]]:
        checkpoint = self._owner.recovery()
        reasons = self._barrier(checkpoint)
        if reasons:
            return checkpoint, reasons
        recoveries = self._recover(checkpoint)
        if any(result.state is PaperMutationRecoveryState.UNRESOLVED for result in recoveries):
            return checkpoint, ("복구되지 않은 Paper mutation이 있어 새 실행을 차단합니다",)
        if recoveries:
            checkpoint = self._owner.recovery()
            reasons = self._barrier(checkpoint)
        return checkpoint, reasons

    def execute_safety(
        self,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperSafetyMutationExecution | BlockedPaperSafetyPlan:
        checkpoint, reasons = self._checkpoint_for_execution()
        if reasons:
            return BlockedPaperSafetyPlan(reasons)
        decision = self._runtime.plan_safety_actions(config)
        reasons = self._barrier(checkpoint)
        if reasons:
            return BlockedPaperSafetyPlan(reasons)
        if isinstance(decision, BlockedPaperSafetyPlan):
            return decision
        if decision.phase is not PaperSafetyPhase.MONITORING:
            _ = self._owner.writer.save_paper_safety_plan(decision)
        stored = tuple(item for item in self._owner.store.paper_safety_plans() if item.plan == decision)
        if not decision.actions:
            return PaperSafetyMutationExecution(
                decision,
                (),
                (),
                checkpoint.mutation_recovery.completed_at,
            )
        if len(stored) != 1:
            return BlockedPaperSafetyPlan(("current-epoch 안전조치 계획 원장이 유일하지 않습니다",))
        repeated_reasons = repeated_acknowledged_safety_action_reasons(
            stored[0],
            self._owner.store.paper_safety_plans(),
            self._owner.store.paper_mutation_intents(),
            self._owner.store.paper_mutation_events(),
        )
        if repeated_reasons:
            return BlockedPaperSafetyPlan(repeated_reasons)
        with self._broker_opener(self._credentials) as broker:
            results = PaperMutationExecutor(
                PaperMutationExecutorDependencies(
                    self._owner.writer,
                    self._owner.store.paper_mutation_events,
                    broker,
                    self._clock,
                )
            ).execute_safety_plan(stored[0])
        reconciled = self._owner.recovery()
        reasons = self._barrier(reconciled)
        if reasons:
            return BlockedPaperSafetyPlan(reasons)
        recoveries = self._recover(reconciled)
        return PaperSafetyMutationExecution(
            decision,
            results,
            recoveries,
            reconciled.mutation_recovery.completed_at,
        )

    def execute_entry(
        self,
        request: PaperOrderAdmissionRequest,
    ) -> PaperEntryMutationExecution | BlockedPaperOrderGateDecision:
        checkpoint, reasons = self._checkpoint_for_execution()
        if reasons:
            return _blocked_entry(reasons)
        decision = self._runtime.evaluate_order(
            latest_bar=request.latest_bar,
            candidate_intent=request.candidate_intent,
            liquidity_allowed_quantity=request.liquidity_allowed_quantity,
            estimated_spread_bps=request.estimated_spread_bps,
            config=request.config,
        )
        reasons = self._barrier(checkpoint)
        if reasons:
            return _blocked_entry(reasons)
        if not isinstance(decision, ApprovedPaperOrderGateDecision):
            return decision
        with self._broker_opener(self._credentials) as broker:
            result = PaperMutationExecutor(
                PaperMutationExecutorDependencies(
                    self._owner.writer,
                    self._owner.store.paper_mutation_events,
                    broker,
                    self._clock,
                )
            ).execute_entry(checkpoint.account_fingerprint, decision.sized_order)
        reconciled = self._owner.recovery()
        reasons = self._barrier(reconciled)
        if reasons:
            return _blocked_entry(reasons)
        recoveries = self._recover(reconciled)
        return PaperEntryMutationExecution(
            decision,
            result,
            recoveries,
            reconciled.mutation_recovery.completed_at,
        )

    def execute_protection(
        self,
        parent_intent_id: IntentId,
    ) -> PaperProtectiveMutationExecution | NoProtectiveExitRequired | BlockedProtectiveExitPlan:
        checkpoint, reasons = self._checkpoint_for_execution()
        if reasons:
            return BlockedProtectiveExitPlan(reasons)
        decision = plan_current_protective_exit(
            self._owner.store.reconciliation_ledger(),
            checkpoint.mutation_recovery.state.broker_state,
            parent_intent_id,
        )
        if isinstance(decision, (BlockedProtectiveExitPlan, NoProtectiveExitRequired)):
            return decision
        _ = self._owner.writer.save_protective_oco_plan(
            decision,
            checkpoint.mutation_recovery.completed_at,
        )
        if checkpoint.connection_epoch != self._owner.stream.connection_epoch:
            return BlockedProtectiveExitPlan((self._stream_epoch_changed_reason,))
        stored = tuple(item for item in self._owner.store.protective_oco_plans() if item.plan == decision)
        if len(stored) != 1:
            return BlockedProtectiveExitPlan(("current-epoch 보호 OCO 계획 원장이 유일하지 않습니다",))
        with self._broker_opener(self._credentials) as broker:
            result = PaperMutationExecutor(
                PaperMutationExecutorDependencies(
                    self._owner.writer,
                    self._owner.store.paper_mutation_events,
                    broker,
                    self._clock,
                )
            ).execute_protective_oco(checkpoint.account_fingerprint, stored[0])
        reconciled = self._owner.recovery()
        reasons = self._barrier(reconciled)
        if reasons:
            return BlockedProtectiveExitPlan(reasons)
        recoveries = self._recover(reconciled)
        return PaperProtectiveMutationExecution(
            decision,
            result,
            recoveries,
            reconciled.mutation_recovery.completed_at,
        )


def _blocked_entry(reasons: tuple[str, ...]) -> BlockedPaperOrderGateDecision:
    return BlockedPaperOrderGateDecision(
        PaperOrderGateState.RECONCILIATION_BLOCKED,
        reasons,
    )
