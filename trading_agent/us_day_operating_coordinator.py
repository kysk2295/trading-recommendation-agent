from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import assert_never

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_arm_request import HermesArmConsumeCommand, HermesArmScope, InvalidHermesArmRequestError
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.paper_execution_models import PaperBrokerState
from trading_agent.paper_operating_mutation_models import PaperEntryMutationExecution
from trading_agent.paper_operating_session import open_paper_operating_session
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    BlockedPaperOrderGateDecision,
)
from trading_agent.us_day_operating_driver import (
    UsDaySessionDriveRequest,
    drive_us_day_session,
    execution_acknowledged,
    has_exact_exposure,
    incident,
    readiness_barrier,
)
from trading_agent.us_day_operating_models import (
    InvalidUsDayOperatingConfigError,
    ProjectedUsDayEvent,
    UsDayArmConsumer,
    UsDayOperatingDraft,
    UsDayOperatingRequest,
    UsDayOperatingResult,
    UsDayOperatingStatus,
    UsDayOperatingTransition,
)
from trading_agent.us_day_operating_projection import project_us_day_actionable, project_us_day_terminal

type UsDaySessionOpener = Callable[
    [AlpacaPaperCredentials, ExecutionStore],
    AbstractContextManager[PaperOperatingSession],
]


@dataclass(frozen=True, slots=True)
class UsDayOperatingCoordinatorConfig:
    arm_consumer: UsDayArmConsumer
    credentials: AlpacaPaperCredentials
    execution_store: ExecutionStore
    delivery_store: HermesDeliveryStore
    session_opener: UsDaySessionOpener = open_paper_operating_session
    max_cycles: int = 8

    def __post_init__(self) -> None:
        if not 1 <= self.max_cycles <= 100:
            raise InvalidUsDayOperatingConfigError


class UsDayOperatingCoordinator:
    __slots__ = ("_config",)

    def __init__(self, config: UsDayOperatingCoordinatorConfig) -> None:
        self._config = config

    def run(self, request: UsDayOperatingRequest) -> UsDayOperatingResult:
        request_reason = _request_reason(request)
        if request_reason is not None:
            return self._finish(request, _blocked(request_reason))
        with self._config.session_opener(self._config.credentials, self._config.execution_store) as session:
            draft = self._run_active(request, session)
        return self._finish(request, draft)

    def _run_active(self, request: UsDayOperatingRequest, session: PaperOperatingSession) -> UsDayOperatingDraft:
        _ = session.recover_mutations()
        initial = session.readiness()
        barrier = readiness_barrier(initial)
        if barrier:
            return _blocked(*barrier, state=initial.broker_state)
        resume = has_exact_exposure(initial, request)
        if not resume:
            decision = session.evaluate_order(request.order_admission)
            match decision:
                case BlockedPaperOrderGateDecision(reasons=reasons):
                    return _blocked(*reasons, state=initial.broker_state)
                case ApprovedPaperOrderGateDecision():
                    pass
                case unreachable:
                    assert_never(unreachable)
        actionable = project_us_day_actionable(request, self._config.delivery_store)
        transitions = [UsDayOperatingTransition.ACTIONABLE]
        parent_intent_id = request.order_admission.candidate_intent.intent_id
        try:
            arm = self._config.arm_consumer.consume(
                HermesArmConsumeCommand(
                    request_id=request.arm_request_id,
                    expected_scope=request_scope(request),
                ),
                request.strategy_version,
            )
        except InvalidHermesArmRequestError as error:
            return _blocked(
                error.reason.value,
                state=initial.broker_state,
                transitions=transitions,
                actionable=actionable,
            )
        if resume:
            transitions.append(UsDayOperatingTransition.ENTRY_ACKNOWLEDGED)
        else:
            entry = session.execute_entry(request.order_admission, arm)
            match entry:
                case BlockedPaperOrderGateDecision(reasons=reasons):
                    return incident(*reasons, transitions=transitions, actionable=actionable)
                case PaperEntryMutationExecution():
                    parent_intent_id = entry.approval.sized_order.intent.intent_id
                    if not execution_acknowledged(entry.result, entry.recoveries):
                        return incident(
                            f"entry_{entry.result.state.value}", transitions=transitions, actionable=actionable
                        )
                    transitions.append(UsDayOperatingTransition.ENTRY_ACKNOWLEDGED)
                case unreachable:
                    assert_never(unreachable)
        return drive_us_day_session(
            UsDaySessionDriveRequest(
                session=session,
                parent_intent_id=parent_intent_id,
                arm=arm,
                transitions=tuple(transitions),
                actionable=actionable,
                max_cycles=self._config.max_cycles,
            )
        )

    def _finish(self, request: UsDayOperatingRequest, draft: UsDayOperatingDraft) -> UsDayOperatingResult:
        terminal = project_us_day_terminal(
            request,
            self._config.delivery_store,
            status=draft.status,
            reasons=draft.reasons,
            root_source_event_id=None if draft.actionable is None else draft.actionable.source_event_id,
            occurred_at=request.evaluated_at,
        )
        transitions = (*draft.transitions, UsDayOperatingTransition.HERMES_RESULT_PROJECTED)
        return UsDayOperatingResult(
            draft.status,
            transitions,
            draft.reasons,
            request.session_id,
            request.strategy_version,
            request.order_admission.candidate_intent.intent_id,
            draft.final_broker_state,
            None if draft.actionable is None else draft.actionable.delivery_id,
            terminal.delivery_id,
        )


def request_scope(request: UsDayOperatingRequest) -> HermesArmScope:
    return HermesArmScope(session_id=request.session_id, lane_id=request.lane_id)


def _request_reason(request: UsDayOperatingRequest) -> str | None:
    if request.strategy_version != request.order_admission.candidate_intent.strategy_version:
        return "strategy_version_mismatch"
    quote_age = request.evaluated_at.astimezone(dt.UTC) - request.quote_observed_at.astimezone(dt.UTC)
    if not dt.timedelta(0) <= quote_age <= dt.timedelta(seconds=5):
        return "stale_quote"
    return None


def _blocked(
    *reasons: str,
    state: PaperBrokerState | None = None,
    transitions: list[UsDayOperatingTransition] | None = None,
    actionable: ProjectedUsDayEvent | None = None,
) -> UsDayOperatingDraft:
    return UsDayOperatingDraft(UsDayOperatingStatus.BLOCKED, tuple(transitions or ()), reasons, state, actionable)
