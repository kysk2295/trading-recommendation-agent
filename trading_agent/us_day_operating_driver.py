from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from trading_agent.paper_execution_models import IntentId, PaperBrokerState
from trading_agent.paper_mutation_arm import PaperMutationArm
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionResult, PaperMutationExecutionState
from trading_agent.paper_mutation_recovery_models import PaperMutationRecoveryResult, PaperMutationRecoveryState
from trading_agent.paper_operating_mutation_models import (
    PaperProtectiveCancelMutationExecution,
    PaperProtectiveMutationExecution,
    PaperSafetyMutationExecution,
)
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.paper_order_gate_models import CompletePaperPortfolio, IncompletePaperPortfolio
from trading_agent.paper_protective_exit import BlockedProtectiveExitPlan, NoProtectiveExitRequired
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.paper_safety_models import BlockedPaperSafetyPlan, PaperSafetyPlan
from trading_agent.paper_trade_update_classification import PaperTradeUpdateIngestionState
from trading_agent.us_day_operating_models import (
    ProjectedUsDayEvent,
    UsDayOperatingDraft,
    UsDayOperatingRequest,
    UsDayOperatingStatus,
    UsDayOperatingTransition,
)


@dataclass(frozen=True, slots=True)
class UsDaySessionDriveRequest:
    session: PaperOperatingSession
    parent_intent_id: IntentId
    arm: PaperMutationArm
    transitions: tuple[UsDayOperatingTransition, ...]
    actionable: ProjectedUsDayEvent
    max_cycles: int


def drive_us_day_session(request: UsDaySessionDriveRequest) -> UsDayOperatingDraft:
    transitions = list(request.transitions)
    for _ in range(request.max_cycles):
        readiness = request.session.readiness()
        barrier = readiness_barrier(readiness)
        if barrier:
            return incident(
                *barrier,
                state=readiness.broker_state,
                transitions=transitions,
                actionable=request.actionable,
            )
        if is_flat(readiness.broker_state):
            transitions.extend((UsDayOperatingTransition.FLAT, UsDayOperatingTransition.RECONCILED))
            return UsDayOperatingDraft(
                UsDayOperatingStatus.COMPLETED,
                tuple(transitions),
                (),
                readiness.broker_state,
                request.actionable,
            )
        protection = request.session.execute_protective_oco(request.parent_intent_id, request.arm)
        protection_reasons = apply_protection(protection, transitions)
        if protection_reasons:
            return incident(
                *protection_reasons,
                state=readiness.broker_state,
                transitions=transitions,
                actionable=request.actionable,
            )
        safety = request.session.plan_safety_actions()
        match safety:
            case BlockedPaperSafetyPlan(reasons=reasons):
                return incident(
                    *reasons,
                    state=readiness.broker_state,
                    transitions=transitions,
                    actionable=request.actionable,
                )
            case PaperSafetyPlan(actions=actions):
                if actions:
                    safety_reasons = _execute_safety(request.session, request.arm)
                    if safety_reasons:
                        return incident(
                            *safety_reasons,
                            state=readiness.broker_state,
                            transitions=transitions,
                            actionable=request.actionable,
                        )
                    continue
            case unreachable:
                assert_never(unreachable)
        try:
            ingestion = request.session.ingest_next(1.0)
        except TimeoutError:
            continue
        if ingestion.state is PaperTradeUpdateIngestionState.QUARANTINED:
            return incident(
                "trade_update_quarantined",
                state=readiness.broker_state,
                transitions=transitions,
                actionable=request.actionable,
            )
    return incident("terminal_timeout", transitions=transitions, actionable=request.actionable)


def readiness_barrier(readiness: PaperRuntimeReadiness) -> tuple[str, ...]:
    return tuple(sorted(set((*readiness.runtime_reasons, *readiness.reconciliation.reasons))))


def has_exact_exposure(readiness: PaperRuntimeReadiness, request: UsDayOperatingRequest) -> bool:
    match readiness.portfolio:
        case CompletePaperPortfolio(exposures=exposures):
            return any(
                exposure.intent_id == request.order_admission.candidate_intent.intent_id for exposure in exposures
            )
        case IncompletePaperPortfolio():
            return False
        case unreachable:
            assert_never(unreachable)


def is_flat(state: PaperBrokerState) -> bool:
    return not state.open_orders and not state.positions and not state.protective_ocos


def execution_acknowledged(
    result: PaperMutationExecutionResult,
    recoveries: tuple[PaperMutationRecoveryResult, ...],
) -> bool:
    match result.state:
        case PaperMutationExecutionState.ACKNOWLEDGED | PaperMutationExecutionState.ALREADY_ACKNOWLEDGED:
            return True
        case PaperMutationExecutionState.AMBIGUOUS:
            return any(recovery.state is PaperMutationRecoveryState.ACKNOWLEDGED for recovery in recoveries)
        case PaperMutationExecutionState.REJECTED:
            return False
        case unreachable:
            assert_never(unreachable)


def apply_protection(
    protection: (
        PaperProtectiveMutationExecution
        | PaperProtectiveCancelMutationExecution
        | NoProtectiveExitRequired
        | BlockedProtectiveExitPlan
    ),
    transitions: list[UsDayOperatingTransition],
) -> tuple[str, ...]:
    match protection:
        case BlockedProtectiveExitPlan(reasons=reasons):
            return reasons
        case NoProtectiveExitRequired():
            return ()
        case PaperProtectiveCancelMutationExecution():
            acknowledged = execution_acknowledged(protection.result, protection.recoveries)
            return () if acknowledged else ("oco_cancel_unresolved",)
        case PaperProtectiveMutationExecution():
            if not execution_acknowledged(protection.result, protection.recoveries):
                return ("protective_oco_unresolved",)
            if UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED not in transitions:
                transitions.append(UsDayOperatingTransition.PROTECTIVE_OCO_ACKNOWLEDGED)
            return ()
        case unreachable:
            assert_never(unreachable)


def incident(
    *reasons: str,
    state: PaperBrokerState | None = None,
    transitions: list[UsDayOperatingTransition] | None = None,
    actionable: ProjectedUsDayEvent | None = None,
) -> UsDayOperatingDraft:
    return UsDayOperatingDraft(UsDayOperatingStatus.INCIDENT, tuple(transitions or ()), reasons, state, actionable)


def _execute_safety(session: PaperOperatingSession, arm: PaperMutationArm) -> tuple[str, ...]:
    execution = session.execute_safety_actions(arm)
    match execution:
        case BlockedPaperSafetyPlan(reasons=reasons):
            return reasons
        case PaperSafetyMutationExecution():
            if all(execution_acknowledged(result, execution.recoveries) for result in execution.results):
                return ()
            return ("safety_mutation_unresolved",)
        case unreachable:
            assert_never(unreachable)
