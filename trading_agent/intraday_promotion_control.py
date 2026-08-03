from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.experiment_ledger_keys import strategy_authority_binding_key
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.intraday_promotion_controller import (
    INTRADAY_PROMOTION_POLICY_VERSION,
    IntradayPromotionControlCommand,
    IntradayPromotionControlResult,
    InvalidIntradayPromotionError,
    _same_session,
    _verified_request,
)
from trading_agent.intraday_promotion_evidence import VerifiedIntradayPromotionEvidence
from trading_agent.intraday_promotion_models import (
    IntradayPromotionApproval,
    IntradayPromotionAssessment,
)
from trading_agent.intraday_promotion_store import (
    load_promotion_approval,
    load_promotion_assessment,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentOperatingMode,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.strategy_authority_models import StrategyAuthorityBinding
from trading_agent.us_equity_calendar import regular_session_bounds


@dataclass(frozen=True, slots=True)
class _ControlContext:
    command: IntradayPromotionControlCommand
    evidence: VerifiedIntradayPromotionEvidence
    target: StrategyLifecycleState
    strategy_id: str
    assessment: IntradayPromotionAssessment
    approval: IntradayPromotionApproval
    ledger: ExperimentLedgerStore


@dataclass(frozen=True, slots=True)
class _BindingSpec:
    strategy_version: str
    strategy_id: str
    target: StrategyLifecycleState
    bound_at: dt.datetime


@dataclass(frozen=True, slots=True)
class _TransitionSpec:
    session_date: dt.date
    evidence: VerifiedIntradayPromotionEvidence
    target: StrategyLifecycleState
    assessment: IntradayPromotionAssessment
    approval: IntradayPromotionApproval
    binding: StrategyAuthorityBinding
    previous: StrategyLifecycleEvent
    previous_key: str


def apply_intraday_promotion(command: IntradayPromotionControlCommand) -> IntradayPromotionControlResult:
    evidence, target, strategy_id = _verified_request(command.request, command.decided_at)
    context = _ControlContext(
        command=command,
        evidence=evidence,
        target=target,
        strategy_id=strategy_id,
        assessment=load_promotion_assessment(command.assessment_path),
        approval=load_promotion_approval(command.approval_path),
        ledger=ExperimentLedgerStore(command.request.experiment_ledger),
    )
    _require_exact_approval(context)
    replay = _existing_replay(context)
    if replay is not None:
        return replay
    events = context.ledger.lifecycle_events(evidence.strategy_version)
    current = context.ledger.lifecycle_state(evidence.strategy_version, command.request.session_date)
    if (
        not events
        or current is None
        or events[-1] != current
        or current.event.to_state is not StrategyLifecycleState.CHALLENGER
    ):
        raise InvalidIntradayPromotionError("challenger_state_required")
    binding = _authority(context)
    event = _event(
        _TransitionSpec(
            command.request.session_date,
            evidence,
            target,
            context.assessment,
            context.approval,
            binding,
            current.event,
            str(current.event_key),
        )
    )
    with context.ledger.writer() as writer:
        binding_created = writer.register_strategy_authority_binding(binding)
        event_created = writer.append_lifecycle_event(event)
    return IntradayPromotionControlResult(
        strategy_version=evidence.strategy_version,
        target_state=target,
        authority_bindings_created=int(binding_created),
        lifecycle_events_created=int(event_created),
        event=event,
    )


def _require_exact_approval(
    context: _ControlContext,
) -> None:
    content = context.assessment.content
    approved = context.approval.content
    request = context.command.request
    if (
        context.evidence.blockers
        or content.strategy_version != context.evidence.strategy_version
        or content.decision_session_date != request.session_date
        or content.target_state is not context.target
        or content.evidence_keys != context.evidence.evidence_keys
        or content.blockers != ("manual_approval_required",)
        or approved.assessment_id != context.assessment.assessment_id
        or approved.strategy_version != content.strategy_version
        or approved.decision_session_date != content.decision_session_date
        or approved.target_state is not content.target_state
        or approved.approved_at < content.assessed_at
        or approved.approved_at > context.command.decided_at
        or not _same_session(approved.approved_at, request.session_date)
    ):
        raise InvalidIntradayPromotionError("manual_approval_invalid")


def _binding(spec: _BindingSpec) -> StrategyAuthorityBinding:
    mode = (
        AgentOperatingMode.ALPACA_PAPER
        if spec.target is StrategyLifecycleState.PAPER_CHAMPION
        else AgentOperatingMode.SHADOW
    )
    return StrategyAuthorityBinding(
        strategy_version=spec.strategy_version,
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id=spec.strategy_id,
        ),
        operating_mode=mode,
        legacy_lane_id=LaneId.INTRADAY_MOMENTUM,
        bound_at=spec.bound_at,
    )


def _authority(context: _ControlContext) -> StrategyAuthorityBinding:
    existing = tuple(
        stored.binding
        for stored in context.ledger.strategy_authority_bindings()
        if stored.binding.strategy_version == context.evidence.strategy_version
    )
    expected = _binding(
        _BindingSpec(
            context.evidence.strategy_version,
            context.strategy_id,
            context.target,
            context.approval.content.approved_at,
        )
    )
    if not existing:
        return expected
    if len(existing) != 1:
        raise InvalidIntradayPromotionError("authority_binding_conflict")
    binding = existing[0]
    if (
        binding.strategy_version != expected.strategy_version
        or binding.strategy_lane != expected.strategy_lane
        or binding.operating_mode is not expected.operating_mode
        or binding.legacy_lane_id is not expected.legacy_lane_id
        or binding.bound_at > context.approval.content.approved_at
    ):
        raise InvalidIntradayPromotionError("authority_binding_conflict")
    return binding


def _event(spec: _TransitionSpec) -> StrategyLifecycleEvent:
    return StrategyLifecycleEvent(
        strategy_version=spec.evidence.strategy_version,
        sequence=spec.previous.sequence + 1,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=StrategyLifecycleState.CHALLENGER,
        to_state=spec.target,
        policy_version=INTRADAY_PROMOTION_POLICY_VERSION,
        decision_session_date=spec.session_date,
        effective_session_date=_next_regular_session(spec.session_date),
        decided_at=spec.approval.content.approved_at,
        evidence_keys=tuple(
            sorted(
                (
                    *spec.evidence.evidence_keys,
                    spec.assessment.assessment_id,
                    spec.approval.approval_id,
                    spec.previous_key,
                    str(strategy_authority_binding_key(spec.binding)),
                )
            )
        ),
        reason_codes=("all_promotion_evidence_verified", "manual_approval_verified"),
        previous_event_key=spec.previous_key,
    )


def _existing_replay(context: _ControlContext) -> IntradayPromotionControlResult | None:
    events = context.ledger.lifecycle_events(context.evidence.strategy_version)
    matches = tuple(
        (index, stored)
        for index, stored in enumerate(events)
        if stored.event.policy_version == INTRADAY_PROMOTION_POLICY_VERSION
        and stored.event.decision_session_date == context.assessment.content.decision_session_date
    )
    if not matches:
        return None
    if len(matches) != 1 or matches[0][0] == 0:
        raise InvalidIntradayPromotionError("promotion_replay_conflict")
    index, stored = matches[0]
    previous = events[index - 1]
    binding = _authority(context)
    expected = _event(
        _TransitionSpec(
            context.assessment.content.decision_session_date,
            context.evidence,
            context.target,
            context.assessment,
            context.approval,
            binding,
            previous.event,
            str(previous.event_key),
        )
    )
    bindings = tuple(
        item
        for item in context.ledger.strategy_authority_bindings()
        if item.binding.strategy_version == context.evidence.strategy_version
    )
    if (
        len(bindings) != 1
        or bindings[0].binding != binding
        or bindings[0].binding_key != strategy_authority_binding_key(binding)
        or stored.event != expected
    ):
        raise InvalidIntradayPromotionError("promotion_replay_conflict")
    return IntradayPromotionControlResult(context.evidence.strategy_version, context.target, 0, 0, stored.event)


def _next_regular_session(session_date: dt.date) -> dt.date:
    for offset in range(1, 11):
        candidate = session_date + dt.timedelta(days=offset)
        if regular_session_bounds(candidate) is not None:
            return candidate
    raise InvalidIntradayPromotionError("next_session_unavailable")


__all__ = ("apply_intraday_promotion",)
