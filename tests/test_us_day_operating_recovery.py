from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from tests.us_day_operating_fixtures import (
    AT,
    NaturalPaperSession,
    OneUseArmConsumer,
    OperatingHarness,
    admission,
    execution,
    operating_request,
)
from trading_agent.paper_execution_models import BrokerOrderId, IntentId, PaperOrderSide
from trading_agent.paper_mutation_arm import PaperMutationArm
from trading_agent.paper_operating_mutation_models import (
    PaperEntryMutationExecution,
    PaperProtectiveCancelMutationExecution,
    PaperProtectiveMutationExecution,
    PaperSafetyMutationExecution,
)
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    PaperExposureKind,
    PaperPortfolioExposure,
)
from trading_agent.paper_protective_exit import BlockedProtectiveExitPlan, NoProtectiveExitRequired
from trading_agent.paper_protective_oco_lifecycle import ProtectiveOcoResizeCancelPlan
from trading_agent.paper_protective_oco_store import ProtectiveOcoPlanKey
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_trade_update_classification import PaperTradeUpdateIngestionResult
from trading_agent.us_day_operating_models import UsDayOperatingStatus, UsDayOperatingTransition


class RestartedPaperSession(NaturalPaperSession):
    def __init__(self, request: PaperOrderAdmissionRequest) -> None:
        super().__init__(request)
        self.phase = 2

    def readiness(self) -> PaperRuntimeReadiness:
        current = super().readiness()
        portfolio = current.portfolio
        assert isinstance(portfolio, CompletePaperPortfolio)
        intent = self.request.candidate_intent
        exposure = PaperPortfolioExposure(
            intent.intent_id,
            intent.symbol,
            PaperExposureKind.OPEN_POSITION,
            Decimal(str(intent.entry_limit)),
            Decimal(1),
        )
        return replace(current, portfolio=replace(portfolio, exposures=(exposure,)))


class PartialFillResizeSession(NaturalPaperSession):
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
        self.protection_calls += 1
        if self.phase == 1:
            return NoProtectiveExitRequired(parent_intent_id)
        if self.phase == 2 and self.protection_calls == 2:
            plan = ProtectiveOcoResizeCancelPlan(
                parent_intent_id,
                ProtectiveOcoPlanKey("d" * 64),
                BrokerOrderId("old-oco"),
                self.request.candidate_intent.symbol,
                AT,
            )
            return PaperProtectiveCancelMutationExecution(plan, execution(), (), AT)
        return super().execute_protective_oco(parent_intent_id, arm)

    def ingest_next(self, timeout_seconds: float) -> PaperTradeUpdateIngestionResult:
        if self.phase == 2 and self.protection_calls == 2:
            result = super().ingest_next(timeout_seconds)
            self.phase = 2
            return result
        return super().ingest_next(timeout_seconds)


class EodFlattenSession(NaturalPaperSession):
    __slots__ = ("safety_calls",)

    def __init__(self, request: PaperOrderAdmissionRequest) -> None:
        super().__init__(request)
        self.safety_calls = 0

    def execute_entry(
        self,
        request: PaperOrderAdmissionRequest,
        arm: PaperMutationArm,
    ) -> PaperEntryMutationExecution:
        result = super().execute_entry(request, arm)
        self.phase = 2
        return result

    def execute_protective_oco(
        self,
        parent_intent_id: IntentId,
        arm: PaperMutationArm,
    ) -> NoProtectiveExitRequired:
        self.protection_calls += 1
        return NoProtectiveExitRequired(parent_intent_id)

    def plan_safety_actions(self, config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG) -> PaperSafetyPlan:
        intent = self.request.candidate_intent
        return PaperSafetyPlan(
            self.readiness().broker_state.account.account_fingerprint,
            AT,
            AT.date(),
            PaperSafetyPhase.EOD_FLATTEN,
            Decimal(0),
            Decimal(0),
            (
                PaperCancelOrderAction(BrokerOrderId("entry-1"), intent.symbol, False),
                PaperClosePositionAction(intent.symbol, PaperOrderSide.SELL, Decimal(1)),
            ),
        )

    def execute_safety_actions(
        self,
        arm: PaperMutationArm,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperSafetyMutationExecution:
        self.safety_calls += 1
        plan = self.plan_safety_actions(config)
        self.phase = 3
        return PaperSafetyMutationExecution(plan, (execution(), execution()), (), AT)


class TimeoutPaperSession(NaturalPaperSession):
    def ingest_next(self, timeout_seconds: float) -> PaperTradeUpdateIngestionResult:
        raise TimeoutError


def test_restart_resumes_exact_intent_without_duplicate_entry(tmp_path: Path) -> None:
    order_admission = admission()
    session = RestartedPaperSession(order_admission)

    result, _ = OperatingHarness(tmp_path, session).run(operating_request(order_admission), OneUseArmConsumer())

    assert result.status is UsDayOperatingStatus.COMPLETED
    assert session.entry_calls == 0
    assert result.transitions.count(UsDayOperatingTransition.ENTRY_ACKNOWLEDGED) == 1
    assert UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED in result.transitions


def test_partial_fill_cancels_old_oco_before_replacing_protection(tmp_path: Path) -> None:
    order_admission = admission()
    session = PartialFillResizeSession(order_admission)

    result, _ = OperatingHarness(tmp_path, session).run(
        operating_request(order_admission),
        OneUseArmConsumer(),
        max_cycles=6,
    )

    assert result.status is UsDayOperatingStatus.COMPLETED
    assert session.protection_calls >= 3
    assert result.transitions.count(UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED) == 1


def test_eod_cancels_entry_then_flattens_position(tmp_path: Path) -> None:
    order_admission = admission()
    session = EodFlattenSession(order_admission)

    result, _ = OperatingHarness(tmp_path, session).run(operating_request(order_admission), OneUseArmConsumer())

    assert result.status is UsDayOperatingStatus.COMPLETED
    assert session.safety_calls == 1
    assert result.final_broker_state is not None
    assert result.final_broker_state.open_orders == ()
    assert result.final_broker_state.positions == ()


def test_terminal_timeout_projects_incident_without_claiming_flat(tmp_path: Path) -> None:
    order_admission = admission()
    session = TimeoutPaperSession(order_admission)

    result, _ = OperatingHarness(tmp_path, session).run(
        operating_request(order_admission),
        OneUseArmConsumer(),
        max_cycles=2,
    )

    assert result.status is UsDayOperatingStatus.INCIDENT
    assert result.reasons == ("terminal_timeout",)
    assert UsDayOperatingTransition.FLAT not in result.transitions
