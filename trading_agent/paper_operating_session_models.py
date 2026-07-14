from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, override

from trading_agent.paper_execution_models import IntentId, PaperOrderIntent
from trading_agent.paper_mutation_arm import PaperMutationArm
from trading_agent.paper_mutation_recovery_models import PaperMutationRecoveryResult
from trading_agent.paper_operating_mutation_models import (
    PaperEntryMutationExecution,
    PaperProtectiveMutationExecution,
    PaperSafetyMutationExecution,
)
from trading_agent.paper_order_gate_models import (
    BlockedPaperOrderGateDecision,
    LatestCompletedBar,
    PaperOrderGateDecision,
)
from trading_agent.paper_protective_exit import (
    BlockedProtectiveExitPlan,
    NoProtectiveExitRequired,
)
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_safety_models import BlockedPaperSafetyPlan, PaperSafetyPlanDecision
from trading_agent.paper_trade_update_classification import PaperTradeUpdateIngestionResult


@dataclass(frozen=True, slots=True)
class PaperOrderAdmissionRequest:
    latest_bar: LatestCompletedBar
    candidate_intent: PaperOrderIntent
    liquidity_allowed_quantity: int
    estimated_spread_bps: float
    config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG


class PaperOperatingSession(Protocol):
    def ingest_next(self, timeout_seconds: float) -> PaperTradeUpdateIngestionResult: ...

    def evaluate_order(
        self,
        request: PaperOrderAdmissionRequest,
    ) -> PaperOrderGateDecision: ...

    def execute_entry(
        self,
        request: PaperOrderAdmissionRequest,
        arm: PaperMutationArm,
    ) -> PaperEntryMutationExecution | BlockedPaperOrderGateDecision: ...

    def plan_safety_actions(
        self,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperSafetyPlanDecision: ...

    def recover_mutations(self) -> tuple[PaperMutationRecoveryResult, ...]: ...

    def execute_safety_actions(
        self,
        arm: PaperMutationArm,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperSafetyMutationExecution | BlockedPaperSafetyPlan: ...

    def execute_protective_oco(
        self,
        parent_intent_id: IntentId,
        arm: PaperMutationArm,
    ) -> PaperProtectiveMutationExecution | NoProtectiveExitRequired | BlockedProtectiveExitPlan: ...


class InactivePaperOperatingSessionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper 단일 운영 세션이 이미 종료되었습니다"


class BusyPaperOperatingSessionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper 단일 운영 세션에서 다른 연산이 진행 중입니다"


class PaperMutationRecoveryBarrierError(RuntimeError):
    __slots__ = ("reasons",)

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__()
        self.reasons = reasons

    @override
    def __str__(self) -> str:
        return "Paper mutation current-epoch 복구 경계가 바뀌었습니다: " + ", ".join(self.reasons)


class PaperPostMutationReconciliationError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Paper mutation 전송 후 current-epoch 대사가 완료되지 않았습니다"
