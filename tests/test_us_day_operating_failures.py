from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from tests.us_day_operating_fixtures import (
    AT,
    NaturalPaperSession,
    OneUseArmConsumer,
    OperatingHarness,
    admission,
    approval,
    execution,
    operating_request,
)
from trading_agent.hermes_arm_request import (
    HermesArmConsumeCommand,
    HermesArmFailure,
    InvalidHermesArmRequestError,
)
from trading_agent.paper_mutation_arm import PaperMutationArm
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState
from trading_agent.paper_mutation_recovery_models import PaperMutationRecoveryResult, PaperMutationRecoveryState
from trading_agent.paper_operating_mutation_models import PaperEntryMutationExecution
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.paper_order_gate_models import BlockedPaperOrderGateDecision, PaperOrderGateState
from trading_agent.paper_reconciliation import ReconciliationResult
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.us_day_operating_models import UsDayOperatingStatus, UsDayOperatingTransition


class BlockedAdmissionSession(NaturalPaperSession):
    __slots__ = ("reason",)

    def __init__(self, request: PaperOrderAdmissionRequest, reason: str) -> None:
        super().__init__(request)
        self.reason = reason

    def evaluate_order(self, request: PaperOrderAdmissionRequest) -> BlockedPaperOrderGateDecision:
        assert request == self.request
        return BlockedPaperOrderGateDecision(PaperOrderGateState.CURRENT_BAR_BLOCKED, (self.reason,))


class ExternalActivitySession(NaturalPaperSession):
    def readiness(self) -> PaperRuntimeReadiness:
        current = super().readiness()
        return replace(current, reconciliation=ReconciliationResult(False, ("external_account_activity",)))


class RejectedEntrySession(NaturalPaperSession):
    def execute_entry(
        self,
        request: PaperOrderAdmissionRequest,
        arm: PaperMutationArm,
    ) -> PaperEntryMutationExecution:
        self.entry_calls += 1
        return PaperEntryMutationExecution(
            approval(request),
            execution(PaperMutationExecutionState.REJECTED),
            (),
            AT,
        )


class AmbiguousEntrySession(NaturalPaperSession):
    def execute_entry(
        self,
        request: PaperOrderAdmissionRequest,
        arm: PaperMutationArm,
    ) -> PaperEntryMutationExecution:
        self.entry_calls += 1
        self.phase = 1
        result = execution(PaperMutationExecutionState.AMBIGUOUS)
        recovery = PaperMutationRecoveryResult(
            result.mutation_key,
            PaperMutationRecoveryState.ACKNOWLEDGED,
            result.broker_order_id,
        )
        return PaperEntryMutationExecution(approval(request), result, (recovery,), AT)


class ConsumedArmConsumer:
    def consume(self, command: HermesArmConsumeCommand, expected_strategy_version: str) -> PaperMutationArm:
        raise InvalidHermesArmRequestError(HermesArmFailure.CONSUMED)


@pytest.mark.parametrize("reason", ("stale_bar", "closed_market", "daily_loss_latched"))
def test_admission_barriers_block_before_arm_or_entry(tmp_path: Path, reason: str) -> None:
    order_admission = admission()
    session = BlockedAdmissionSession(order_admission, reason)
    arm = OneUseArmConsumer()

    result, _ = OperatingHarness(tmp_path, session).run(operating_request(order_admission), arm)

    assert result.status is UsDayOperatingStatus.BLOCKED
    assert result.reasons == (reason,)
    assert result.transitions == (UsDayOperatingTransition.HERMES_RESULT_PROJECTED,)
    assert session.entry_calls == 0
    assert arm.calls == []


@pytest.mark.parametrize(
    ("quote_observed_at", "strategy_version", "reason"),
    (
        (AT - dt.timedelta(seconds=6), None, "stale_quote"),
        (AT - dt.timedelta(seconds=1), "different-version", "strategy_version_mismatch"),
    ),
)
def test_request_barriers_block_before_session_mutation(
    tmp_path: Path,
    quote_observed_at: dt.datetime,
    strategy_version: str | None,
    reason: str,
) -> None:
    order_admission = admission()
    session = NaturalPaperSession(order_admission)
    request = operating_request(order_admission, quote_observed_at, strategy_version)

    result, _ = OperatingHarness(tmp_path, session).run(request, OneUseArmConsumer())

    assert result.status is UsDayOperatingStatus.BLOCKED
    assert result.reasons == (reason,)
    assert session.entry_calls == 0


def test_external_account_activity_fails_closed_before_admission(tmp_path: Path) -> None:
    order_admission = admission()
    session = ExternalActivitySession(order_admission)

    result, _ = OperatingHarness(tmp_path, session).run(operating_request(order_admission), OneUseArmConsumer())

    assert result.status is UsDayOperatingStatus.BLOCKED
    assert result.reasons == ("external_account_activity",)
    assert session.entry_calls == 0


def test_entry_rejection_projects_incident_without_protection(tmp_path: Path) -> None:
    order_admission = admission()
    session = RejectedEntrySession(order_admission)

    result, _ = OperatingHarness(tmp_path, session).run(operating_request(order_admission), OneUseArmConsumer())

    assert result.status is UsDayOperatingStatus.INCIDENT
    assert result.reasons == ("entry_rejected",)
    assert session.entry_calls == 1
    assert session.protection_calls == 0


def test_ambiguous_entry_with_targeted_recovery_continues_to_terminal(tmp_path: Path) -> None:
    order_admission = admission()
    session = AmbiguousEntrySession(order_admission)

    result, _ = OperatingHarness(tmp_path, session).run(operating_request(order_admission), OneUseArmConsumer())

    assert result.status is UsDayOperatingStatus.COMPLETED
    assert UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED in result.transitions


def test_consumed_arm_blocks_duplicate_entry(tmp_path: Path) -> None:
    order_admission = admission()
    session = NaturalPaperSession(order_admission)

    result, _ = OperatingHarness(tmp_path, session).run(operating_request(order_admission), ConsumedArmConsumer())

    assert result.status is UsDayOperatingStatus.BLOCKED
    assert result.reasons == (HermesArmFailure.CONSUMED.value,)
    assert result.transitions == (
        UsDayOperatingTransition.ACTIONABLE,
        UsDayOperatingTransition.HERMES_RESULT_PROJECTED,
    )
    assert session.entry_calls == 0
