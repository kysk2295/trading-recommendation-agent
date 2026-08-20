from __future__ import annotations

import datetime as dt
from decimal import Decimal

from trading_agent.day_forward_probe_bridge import DaySignalBlocked
from trading_agent.day_forward_trial_identity import DayForwardExitReason, DayForwardTrialEventKind
from trading_agent.day_forward_trial_ledger import DayForwardTrialState
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.generated_strategy_execution import GeneratedStrategyExecutionError
from trading_agent.us_forward_shadow_artifacts import (
    UsForwardShadowOutcomeLeg,
    build_us_forward_shadow_outcome_artifact,
    build_us_forward_shadow_signal_artifact,
)
from trading_agent.us_forward_shadow_generated import evaluate_generated_signal
from trading_agent.us_forward_shadow_models import (
    UsForwardShadowCapsuleResult,
    UsForwardShadowStatus,
    UsForwardShadowTick,
)
from trading_agent.us_forward_shadow_services import (
    InvalidUsForwardShadowRuntimeError,
    UsForwardShadowServices,
)
from trading_agent.us_forward_shadow_trial import (
    build_us_forward_shadow_event,
    build_us_forward_shadow_outcome_ref,
    build_us_forward_shadow_trial,
    completed_bar_at,
)


def advance_us_forward_shadow_capsule(
    tick: UsForwardShadowTick,
    capsule: StrategyCapsule,
    state: DayForwardTrialState | None,
    services: UsForwardShadowServices,
    *,
    evaluation_at: dt.datetime,
) -> UsForwardShadowCapsuleResult:
    if state is None:
        version = services.ledger.reader().day_hypothesis_version(capsule.hypothesis_version_id)
        if version is None:
            raise InvalidUsForwardShadowRuntimeError("hypothesis_version_missing")
        trial = build_us_forward_shadow_trial(
            capsule,
            version.version.source_refs,
            tick,
            evaluation_at=evaluation_at,
        )
        with services.ledger.writer() as writer:
            _ = writer.register_day_forward_trial(trial)
        return _result(capsule, trial.trial_id, UsForwardShadowStatus.REGISTERED)
    if state.terminal or any(event.completed_bar_id == tick.completed_bar_id for event in state.events):
        return _replayed_result(capsule, state)
    if completed_bar_at(tick) < state.trial.first_eligible_completed_bar_at:
        return _result(capsule, state.trial.trial_id, UsForwardShadowStatus.REGISTERED)
    if not state.events and completed_bar_at(tick) > state.trial.first_eligible_completed_bar_at:
        return _append_single(
            tick,
            capsule,
            state,
            DayForwardTrialEventKind.CENSORED,
            services,
            evaluation_at=evaluation_at,
        )
    if state.events and tick.completed_bar_sequence > state.events[-1].completed_bar_sequence + 1:
        return _append_single(
            tick,
            capsule,
            state,
            DayForwardTrialEventKind.CENSORED,
            services,
            evaluation_at=evaluation_at,
        )
    if any(event.event_kind is DayForwardTrialEventKind.ENTRY for event in state.events):
        return _advance_entered(tick, capsule, state, services, evaluation_at=evaluation_at)
    return _observe_generated(tick, capsule, state, services, evaluation_at=evaluation_at)


def _observe_generated(
    tick: UsForwardShadowTick,
    capsule: StrategyCapsule,
    state: DayForwardTrialState,
    services: UsForwardShadowServices,
    *,
    evaluation_at: dt.datetime,
) -> UsForwardShadowCapsuleResult:
    try:
        projection = evaluate_generated_signal(tick, capsule, services, evaluation_at=evaluation_at)
    except GeneratedStrategyExecutionError:
        return _append_single(
            tick,
            capsule,
            state,
            DayForwardTrialEventKind.FAILED,
            services,
            evaluation_at=evaluation_at,
            reason_codes=("generated_strategy_failed",),
        )
    if projection is None:
        return _append_single(
            tick,
            capsule,
            state,
            DayForwardTrialEventKind.NO_SIGNAL,
            services,
            evaluation_at=evaluation_at,
        )
    if isinstance(projection, DaySignalBlocked):
        return _append_single(
            tick,
            capsule,
            state,
            DayForwardTrialEventKind.BLOCKED,
            services,
            evaluation_at=evaluation_at,
            reason_codes=(projection.reason.value,),
        )
    signal_artifact = build_us_forward_shadow_signal_artifact(
        trial_id=state.trial.trial_id,
        capsule_id=capsule.capsule_id,
        completed_bar_id=tick.completed_bar_id,
        signal=projection,
    )
    _ = services.shadow_artifacts.publish_signal(signal_artifact)
    signal_event = build_us_forward_shadow_event(
        state.trial,
        tick,
        DayForwardTrialEventKind.SIGNAL,
        evaluation_at=evaluation_at,
        sequence=len(state.events) + 1,
        previous_event_id=state.events[-1].event_id if state.events else None,
    )
    entry_event = build_us_forward_shadow_event(
        state.trial,
        tick,
        DayForwardTrialEventKind.ENTRY,
        evaluation_at=evaluation_at,
        sequence=signal_event.sequence + 1,
        previous_event_id=signal_event.event_id,
    )
    with services.ledger.writer() as writer:
        stored_signal = writer.append_day_forward_trial_event(signal_event)
        stored_entry = writer.append_day_forward_trial_event(entry_event)
    return _result(
        capsule,
        state.trial.trial_id,
        UsForwardShadowStatus.ENTERED,
        event_ids=(stored_signal.event_id, stored_entry.event_id),
    )


def _advance_entered(
    tick: UsForwardShadowTick,
    capsule: StrategyCapsule,
    state: DayForwardTrialState,
    services: UsForwardShadowServices,
    *,
    evaluation_at: dt.datetime,
) -> UsForwardShadowCapsuleResult:
    signal_artifact = services.shadow_artifacts.signal(state.trial.trial_id)
    signal = signal_artifact.signal
    targets = {target.label: target.price for target in signal.targets}
    if tuple(targets) != ("r1", "r2"):
        raise InvalidUsForwardShadowRuntimeError("host_targets_invalid")
    r1_event = next(
        (event for event in state.events if event.reason_codes == ("target_r1_reached",)),
        None,
    )
    stop_hit = tick.bars[-1].low <= float(signal.stop_price)
    r1_hit = tick.bars[-1].high >= float(targets["r1"])
    r2_hit = tick.bars[-1].high >= float(targets["r2"])
    if stop_hit:
        return _exit_entered(
            tick,
            capsule,
            state,
            signal_artifact.artifact_id,
            signal.entry_price,
            signal.stop_price,
            targets,
            DayForwardExitReason.STOP,
            r1_completed_bar_id=None if r1_event is None else r1_event.completed_bar_id,
            services=services,
            evaluation_at=evaluation_at,
        )
    if r2_hit:
        return _exit_entered(
            tick,
            capsule,
            state,
            signal_artifact.artifact_id,
            signal.entry_price,
            signal.stop_price,
            targets,
            DayForwardExitReason.TARGET,
            r1_completed_bar_id=tick.completed_bar_id if r1_event is None else r1_event.completed_bar_id,
            services=services,
            evaluation_at=evaluation_at,
        )
    if r1_hit and r1_event is None:
        return _append_single(
            tick,
            capsule,
            state,
            DayForwardTrialEventKind.OBSERVED,
            services,
            evaluation_at=evaluation_at,
            reason_codes=("target_r1_reached",),
        )
    return _append_single(
        tick,
        capsule,
        state,
        DayForwardTrialEventKind.OBSERVED,
        services,
        evaluation_at=evaluation_at,
    )


def _exit_entered(
    tick: UsForwardShadowTick,
    capsule: StrategyCapsule,
    state: DayForwardTrialState,
    signal_artifact_id: str,
    entry_price: Decimal,
    stop_price: Decimal,
    targets: dict[str, Decimal],
    exit_reason: DayForwardExitReason,
    *,
    r1_completed_bar_id: str | None,
    services: UsForwardShadowServices,
    evaluation_at: dt.datetime,
) -> UsForwardShadowCapsuleResult:
    legs = _modeled_legs(
        tick,
        entry_price,
        stop_price,
        targets,
        exit_reason,
        r1_completed_bar_id=r1_completed_bar_id,
    )
    costs = Decimal("2") * (capsule.cost_model.commission_bps + capsule.cost_model.slippage_bps)
    outcome = build_us_forward_shadow_outcome_artifact(
        trial_id=state.trial.trial_id,
        signal_artifact_id=signal_artifact_id,
        exit_completed_bar_id=tick.completed_bar_id,
        entry_price=entry_price,
        legs=legs,
        round_trip_cost_bps=costs,
        exit_reason=exit_reason,
        recorded_at=evaluation_at,
    )
    _ = services.shadow_artifacts.publish_outcome(outcome)
    event = build_us_forward_shadow_event(
        state.trial,
        tick,
        DayForwardTrialEventKind.EXIT,
        evaluation_at=evaluation_at,
        sequence=len(state.events) + 1,
        previous_event_id=state.events[-1].event_id,
        exit_reason=exit_reason,
        outcome_ref=build_us_forward_shadow_outcome_ref(outcome),
    )
    with services.ledger.writer() as writer:
        stored = writer.append_day_forward_trial_event(event)
    return _result(
        capsule,
        state.trial.trial_id,
        UsForwardShadowStatus.EXITED,
        event_ids=(stored.event_id,),
        outcome_id=outcome.outcome_id,
    )


def _modeled_legs(
    tick: UsForwardShadowTick,
    entry_price: Decimal,
    stop_price: Decimal,
    targets: dict[str, Decimal],
    exit_reason: DayForwardExitReason,
    *,
    r1_completed_bar_id: str | None,
) -> tuple[UsForwardShadowOutcomeLeg, UsForwardShadowOutcomeLeg]:
    r1_targeted = r1_completed_bar_id is not None
    if exit_reason is DayForwardExitReason.TARGET:
        r1_price = targets["r1"]
        r2_price = targets["r2"]
        r1_reason = DayForwardExitReason.TARGET
        r2_reason = DayForwardExitReason.TARGET
    elif r1_targeted:
        r1_price = targets["r1"]
        r2_price = stop_price
        r1_reason = DayForwardExitReason.TARGET
        r2_reason = DayForwardExitReason.STOP
    else:
        r1_price = stop_price
        r2_price = stop_price
        r1_reason = DayForwardExitReason.STOP
        r2_reason = DayForwardExitReason.STOP
    r1_bar_id = tick.completed_bar_id if r1_completed_bar_id is None else r1_completed_bar_id
    return (
        UsForwardShadowOutcomeLeg(
            target_label="r1",
            exit_completed_bar_id=r1_bar_id,
            exit_price=r1_price,
            exit_reason=r1_reason,
            weight=Decimal("0.5"),
            gross_return=(r1_price - entry_price) / entry_price,
        ),
        UsForwardShadowOutcomeLeg(
            target_label="r2",
            exit_completed_bar_id=tick.completed_bar_id,
            exit_price=r2_price,
            exit_reason=r2_reason,
            weight=Decimal("0.5"),
            gross_return=(r2_price - entry_price) / entry_price,
        ),
    )
def _append_single(
    tick: UsForwardShadowTick,
    capsule: StrategyCapsule,
    state: DayForwardTrialState,
    kind: DayForwardTrialEventKind,
    services: UsForwardShadowServices,
    *,
    evaluation_at: dt.datetime,
    reason_codes: tuple[str, ...] = (),
) -> UsForwardShadowCapsuleResult:
    event = build_us_forward_shadow_event(
        state.trial,
        tick,
        kind,
        evaluation_at=evaluation_at,
        sequence=len(state.events) + 1,
        previous_event_id=state.events[-1].event_id if state.events else None,
        reason_codes=("completed_bar_gap",) if kind is DayForwardTrialEventKind.CENSORED else reason_codes,
    )
    with services.ledger.writer() as writer:
        stored = writer.append_day_forward_trial_event(event)
    return _result(
        capsule,
        state.trial.trial_id,
        _status_for(stored.event_kind),
        event_ids=(stored.event_id,),
        reason_codes=stored.reason_codes,
    )


def _replayed_result(capsule: StrategyCapsule, state: DayForwardTrialState) -> UsForwardShadowCapsuleResult:
    current = state.events[-1:] if state.events else ()
    outcome_id = current[0].outcome_ref.outcome_id if current and current[0].outcome_ref else None
    return _result(
        capsule,
        state.trial.trial_id,
        UsForwardShadowStatus.REPLAYED,
        event_ids=tuple(event.event_id for event in current),
        outcome_id=outcome_id,
    )


def _status_for(kind: DayForwardTrialEventKind) -> UsForwardShadowStatus:
    return {
        DayForwardTrialEventKind.NO_SIGNAL: UsForwardShadowStatus.NO_SIGNAL,
        DayForwardTrialEventKind.OBSERVED: UsForwardShadowStatus.OBSERVED,
        DayForwardTrialEventKind.CENSORED: UsForwardShadowStatus.CENSORED,
        DayForwardTrialEventKind.BLOCKED: UsForwardShadowStatus.BLOCKED,
        DayForwardTrialEventKind.FAILED: UsForwardShadowStatus.FAILED,
    }.get(kind, UsForwardShadowStatus.REPLAYED)


def _result(
    capsule: StrategyCapsule,
    trial_id: str,
    status: UsForwardShadowStatus,
    *,
    event_ids: tuple[str, ...] = (),
    outcome_id: str | None = None,
    reason_codes: tuple[str, ...] = (),
) -> UsForwardShadowCapsuleResult:
    return UsForwardShadowCapsuleResult(
        capsule_id=capsule.capsule_id,
        trial_id=trial_id,
        status=status,
        event_ids=event_ids,
        outcome_id=outcome_id,
        reason_codes=reason_codes,
    )


__all__ = ("advance_us_forward_shadow_capsule",)
