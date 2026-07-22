from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from tests.paper_runtime_fixtures import account, candidate, latest_bar, market_clock
from tests.test_paper_mutation_executor import _protective_plan
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import PaperOrderStreamHeartbeat, PaperStreamEpoch
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_arm_request import HermesArmConsumeCommand
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperBrokerState,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
    SizedPaperOrder,
)
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionResult, PaperMutationExecutionState
from trading_agent.paper_mutation_keys import PaperMutationKey
from trading_agent.paper_mutation_recovery_models import PaperMutationRecoveryResult
from trading_agent.paper_operating_mutation_models import (
    PaperEntryMutationExecution,
    PaperProtectiveCancelMutationExecution,
    PaperProtectiveMutationExecution,
    PaperSafetyMutationExecution,
)
from trading_agent.paper_operating_session_models import PaperOperatingSession, PaperOrderAdmissionRequest
from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    CompletePaperPortfolio,
    PaperOrderGateDecision,
)
from trading_agent.paper_protective_exit import BlockedProtectiveExitPlan, NoProtectiveExitRequired
from trading_agent.paper_reconciliation import ReconciliationResult
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.paper_safety_models import BlockedPaperSafetyPlan, PaperSafetyPhase, PaperSafetyPlan
from trading_agent.paper_trade_update_classification import (
    PaperTradeUpdateIngestionResult,
    PaperTradeUpdateIngestionState,
)
from trading_agent.trade_update_receipt_models import TradeUpdateReceiptKey
from trading_agent.us_day_operating_coordinator import UsDayOperatingCoordinator, UsDayOperatingCoordinatorConfig
from trading_agent.us_day_operating_models import UsDayArmConsumer, UsDayOperatingRequest, UsDayOperatingResult

AT = dt.datetime(2026, 7, 14, 13, 36, 4, tzinfo=dt.UTC)


class OneUseArmConsumer:
    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls: list[HermesArmConsumeCommand] = []

    def consume(self, command: HermesArmConsumeCommand, expected_strategy_version: str) -> PaperMutationArm:
        assert expected_strategy_version
        self.calls.append(command)
        return PaperMutationArm(PAPER_MUTATION_ARM_VALUE)


class NaturalPaperSession:
    """Mutable fake models broker progression while preserving the production session contract."""

    __slots__ = ("entry_calls", "ingest_calls", "phase", "protected", "protection_calls", "request")

    def __init__(self, request: PaperOrderAdmissionRequest) -> None:
        self.request = request
        self.phase = 0
        self.protected = False
        self.entry_calls = 0
        self.protection_calls = 0
        self.ingest_calls = 0

    def recover_mutations(self) -> tuple[PaperMutationRecoveryResult, ...]:
        return ()

    def readiness(self) -> PaperRuntimeReadiness:
        return readiness(self.request, self.phase)

    def evaluate_order(self, request: PaperOrderAdmissionRequest) -> PaperOrderGateDecision:
        assert request == self.request
        return approval(request)

    def execute_entry(
        self,
        request: PaperOrderAdmissionRequest,
        arm: PaperMutationArm,
    ) -> PaperEntryMutationExecution:
        assert request == self.request
        assert arm.value == PAPER_MUTATION_ARM_VALUE
        self.entry_calls += 1
        self.phase = 1
        return PaperEntryMutationExecution(approval(request), execution(), (), AT)

    def execute_protective_oco(
        self,
        parent_intent_id: IntentId,
        arm: PaperMutationArm,
    ) -> (
        PaperProtectiveMutationExecution
        | PaperProtectiveCancelMutationExecution
        | NoProtectiveExitRequired
        | BlockedProtectiveExitPlan
    ):
        assert parent_intent_id == self.request.candidate_intent.intent_id
        assert arm.value == PAPER_MUTATION_ARM_VALUE
        self.protection_calls += 1
        if self.phase != 2 or self.protected:
            return NoProtectiveExitRequired(parent_intent_id)
        self.protected = True
        return PaperProtectiveMutationExecution(_protective_plan(), execution(), (), AT)

    def plan_safety_actions(self, config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG) -> PaperSafetyPlan:
        return PaperSafetyPlan(
            account().account_fingerprint,
            AT,
            AT.date(),
            PaperSafetyPhase.MONITORING,
            Decimal(0),
            Decimal(0),
            (),
        )

    def execute_safety_actions(
        self,
        arm: PaperMutationArm,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperSafetyMutationExecution | BlockedPaperSafetyPlan:
        raise AssertionError("natural fixture must not execute EOD safety")

    def ingest_next(self, timeout_seconds: float) -> PaperTradeUpdateIngestionResult:
        assert timeout_seconds == 1.0
        self.ingest_calls += 1
        self.phase += 1
        return PaperTradeUpdateIngestionResult(
            TradeUpdateReceiptKey(f"receipt-{self.ingest_calls}"),
            PaperTradeUpdateIngestionState.ACCEPTED,
            True,
            None,
        )


@dataclass(frozen=True, slots=True)
class OperatingHarness:
    tmp_path: Path
    session: PaperOperatingSession

    def run(
        self,
        request: UsDayOperatingRequest,
        arm_consumer: UsDayArmConsumer,
        max_cycles: int = 4,
    ) -> tuple[UsDayOperatingResult, HermesDeliveryStore]:
        execution_store = ExecutionStore(self.tmp_path / "execution.sqlite3")
        delivery_store = HermesDeliveryStore(self.tmp_path / "delivery.sqlite3")

        @contextmanager
        def opener(_: AlpacaPaperCredentials, __: ExecutionStore) -> Iterator[PaperOperatingSession]:
            yield self.session

        coordinator = UsDayOperatingCoordinator(
            UsDayOperatingCoordinatorConfig(
                arm_consumer=arm_consumer,
                credentials=AlpacaPaperCredentials("test-key", "test-secret"),
                execution_store=execution_store,
                delivery_store=delivery_store,
                session_opener=opener,
                max_cycles=max_cycles,
            )
        )
        return coordinator.run(request), delivery_store


def admission() -> PaperOrderAdmissionRequest:
    return PaperOrderAdmissionRequest(latest_bar(), candidate(), 100, 20.0)


def operating_request(
    request: PaperOrderAdmissionRequest,
    quote_observed_at: dt.datetime = AT - dt.timedelta(seconds=1),
    strategy_version: str | None = None,
) -> UsDayOperatingRequest:
    return UsDayOperatingRequest(
        arm_request_id="a" * 64,
        session_id="XNYS-2026-07-14",
        strategy_version=strategy_version or request.candidate_intent.strategy_version,
        order_admission=request,
        quote_observed_at=quote_observed_at,
        evaluated_at=AT,
        actionable_payload_sha256="b" * 64,
    )


def approval(request: PaperOrderAdmissionRequest) -> ApprovedPaperOrderGateDecision:
    intent = request.candidate_intent
    return ApprovedPaperOrderGateDecision(SizedPaperOrder(intent, 1, 1.0, 1.0, intent.entry_limit))


def execution(
    state: PaperMutationExecutionState = PaperMutationExecutionState.ACKNOWLEDGED,
) -> PaperMutationExecutionResult:
    broker_order_id = None if state is PaperMutationExecutionState.REJECTED else BrokerOrderId("broker-order-1")
    return PaperMutationExecutionResult(PaperMutationKey("c" * 64), state, broker_order_id)


def readiness(request: PaperOrderAdmissionRequest, phase: int) -> PaperRuntimeReadiness:
    intent = request.candidate_intent
    order = PaperOrderSnapshot(
        BrokerOrderId("entry-1"),
        intent.intent_id,
        intent.symbol,
        intent.side,
        "accepted",
        Decimal(1),
        Decimal(0 if phase == 1 else 1),
        Decimal(str(intent.entry_limit)),
        "day",
        False,
    )
    orders = (order,) if phase in (1, 2) else ()
    positions = (
        (PaperPositionSnapshot(intent.symbol, Decimal(1), Decimal(str(intent.entry_limit))),)
        if phase == 2
        else ()
    )
    broker = PaperBrokerState(account(), orders, positions)
    portfolio = CompletePaperPortfolio(
        AT,
        "ACTIVE",
        False,
        Decimal(30_000),
        Decimal(30_000),
        Decimal(60_000),
        (),
    )
    heartbeat = PaperOrderStreamHeartbeat(PaperStreamEpoch("epoch-1"), AT, AT, AT)
    return PaperRuntimeReadiness(broker, market_clock(), heartbeat, ReconciliationResult(True, ()), portfolio)
