from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, override

from pydantic import ValidationError

from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    EVALUATOR_VERSION,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
    strategy_version_identity,
)
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    StrategyVersionRegistration,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
    StoredStrategyLifecycleEvent,
)
from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_daily_snapshot_key,
    lane_manifest_key,
)
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import (
    INTRADAY_MANIFEST,
    current_intraday_experiment_scope,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lane_review_models import (
    CURRENT_LANE_REVIEWER_VERSION,
    LaneReviewerAction,
    LaneReviewEvent,
)
from trading_agent.lane_review_store import (
    InvalidLaneReviewSourceError,
    LaneReviewReader,
    UnsupportedLaneReviewSchemaError,
)
from trading_agent.strategy_factory import StrategyMode
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

CURRENT_LIFECYCLE_CONTROLLER_POLICY: Final = "lifecycle_controller_v1"
PROMOTION_BLOCKERS: Final = (
    "broker_shadow_promotion_evidence_missing",
    "dsr_pbo_evidence_missing",
    "parameter_plateau_evidence_missing",
    "sip_validation_evidence_missing",
)
_SUSPEND_REASON_CODES: Final = (
    "five_day_clear_degradation",
    "review_evidence_verified",
)
_SUSPENDIBLE_STATES: Final = frozenset(
    {
        StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        StrategyLifecycleState.EXPERIMENTAL_PAPER,
        StrategyLifecycleState.CHALLENGER,
        StrategyLifecycleState.SHADOW_CHAMPION,
        StrategyLifecycleState.PAPER_CHAMPION,
    }
)
_ORB_CONTRACT: Final = strategy_contract(StrategyMode.ORB)
_ORB_SCOPE: Final = current_intraday_experiment_scope(_ORB_CONTRACT.hypothesis_id)
_ORB_SCOPE_KEY: Final = experiment_scope_key(_ORB_SCOPE)


class LifecycleControllerOutcome(StrEnum):
    NO_CHANGE = "no_change"
    BLOCKED = "blocked"
    TRANSITIONED = "transitioned"


class InvalidLifecycleControllerSourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Lifecycle Controller가 exact immutable evidence를 확인하지 못했습니다"


@dataclass(frozen=True, slots=True)
class LifecycleControllerResult:
    outcome: LifecycleControllerOutcome
    created: bool
    session_date: dt.date
    from_state: StrategyLifecycleState
    to_state: StrategyLifecycleState | None
    reason_codes: tuple[str, ...]
    blockers: tuple[str, ...]
    event: StrategyLifecycleEvent | None


@dataclass(frozen=True, slots=True)
class _VerifiedControllerSource:
    snapshot: LaneDailySnapshot
    snapshot_key: str
    review: LaneReviewEvent
    review_key: str
    lifecycle_events: tuple[StoredStrategyLifecycleEvent, ...]
    current: StoredStrategyLifecycleEvent


def control_intraday_orb_lifecycle(
    *,
    lane_registry: LaneRegistryReader,
    review_ledger: LaneReviewReader,
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    decided_at: dt.datetime,
) -> LifecycleControllerResult:
    try:
        source = _verified_source(
            lane_registry,
            review_ledger,
            experiment_ledger,
            session_date,
            decided_at,
        )
        replay = _existing_controller_replay(source, session_date)
        if replay is not None:
            return replay
        latest = source.lifecycle_events[-1]
        if latest != source.current or latest.event.effective_session_date > session_date:
            raise InvalidLifecycleControllerSourceError
        decision = _closed_policy_decision(source, session_date)
        if decision is not None:
            return decision
        event = _suspend_event(source, session_date, decided_at)
    except InvalidLifecycleControllerSourceError:
        raise
    except (
        InvalidExperimentLedgerSourceError,
        InvalidLaneRegistrySourceError,
        UnsupportedLaneRegistrySchemaError,
        InvalidLaneReviewSourceError,
        UnsupportedLaneReviewSchemaError,
        ValidationError,
        sqlite3.Error,
        OSError,
        ValueError,
    ):
        raise InvalidLifecycleControllerSourceError from None

    with experiment_ledger.writer() as writer:
        created = writer.append_lifecycle_event(event)
    return LifecycleControllerResult(
        outcome=LifecycleControllerOutcome.TRANSITIONED,
        created=created,
        session_date=session_date,
        from_state=source.current.event.to_state,
        to_state=StrategyLifecycleState.SUSPENDED,
        reason_codes=_SUSPEND_REASON_CODES,
        blockers=(),
        event=event,
    )


def _verified_source(
    lane_registry: LaneRegistryReader,
    review_ledger: LaneReviewReader,
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    decided_at: dt.datetime,
) -> _VerifiedControllerSource:
    if (
        not _aware(decided_at)
        or decided_at.astimezone(NEW_YORK).date() != session_date
        or regular_session_bounds(session_date) is None
    ):
        raise InvalidLifecycleControllerSourceError
    _require_exact_lane_contracts(lane_registry)
    stored_snapshot = lane_registry.daily_snapshot(LaneId.INTRADAY_MOMENTUM, session_date)
    if stored_snapshot is None:
        raise InvalidLifecycleControllerSourceError
    snapshot = stored_snapshot.snapshot
    snapshot_key = str(lane_daily_snapshot_key(snapshot))
    bounds = regular_session_bounds(session_date)
    if bounds is None:
        raise InvalidLifecycleControllerSourceError
    if (
        str(stored_snapshot.snapshot_key) != snapshot_key
        or snapshot.lane_id is not LaneId.INTRADAY_MOMENTUM
        or snapshot.session_date != session_date
        or snapshot.manifest_key != lane_manifest_key(INTRADAY_MANIFEST)
        or snapshot.experiment_scope_keys != (_ORB_SCOPE_KEY,)
        or snapshot.finalized_at < bounds[1]
        or snapshot.open_order_count != 0
        or snapshot.open_position_count != 0
        or snapshot.planned_open_risk != 0
    ):
        raise InvalidLifecycleControllerSourceError

    if not review_ledger.is_initialized():
        raise InvalidLifecycleControllerSourceError
    stored_review = review_ledger.review_event(
        snapshot_key,
        _ORB_SCOPE_KEY,
        CURRENT_LANE_REVIEWER_VERSION,
    )
    if stored_review is None:
        raise InvalidLifecycleControllerSourceError
    review = stored_review.event
    review_key = str(lane_review_event_key(review))
    if (
        str(stored_review.event_key) != review_key
        or review.lane_id is not LaneId.INTRADAY_MOMENTUM
        or review.session_date != session_date
        or review.snapshot_key != snapshot_key
        or review.experiment_scope_key != _ORB_SCOPE_KEY
        or review.evaluator_version != EVALUATOR_VERSION
        or review.reviewer_version != CURRENT_LANE_REVIEWER_VERSION
        or review.reviewer_action is not _expected_reviewer_action(review.adaptive_action)
        or snapshot.finalized_at > review.reviewed_at
        or review.reviewed_at > decided_at
    ):
        raise InvalidLifecycleControllerSourceError

    version = _require_exact_experiment_lineage(
        experiment_ledger,
        review.strategy_version,
    )
    lifecycle_events = experiment_ledger.lifecycle_events(version.strategy_version)
    current = experiment_ledger.lifecycle_state(version.strategy_version, session_date)
    if not lifecycle_events or current is None:
        raise InvalidLifecycleControllerSourceError
    return _VerifiedControllerSource(
        snapshot=snapshot,
        snapshot_key=snapshot_key,
        review=review,
        review_key=review_key,
        lifecycle_events=lifecycle_events,
        current=current,
    )


def _require_exact_lane_contracts(lane_registry: LaneRegistryReader) -> None:
    if not lane_registry.is_initialized():
        raise InvalidLifecycleControllerSourceError
    manifests = tuple(
        stored
        for stored in lane_registry.manifests()
        if stored.manifest.lane_id is LaneId.INTRADAY_MOMENTUM
        and stored.manifest.manifest_version == INTRADAY_MANIFEST.manifest_version
    )
    scopes = tuple(
        stored for stored in lane_registry.experiment_scopes() if stored.scope.hypothesis_id == _ORB_SCOPE.hypothesis_id
    )
    if (
        len(manifests) != 1
        or manifests[0].manifest != INTRADAY_MANIFEST
        or manifests[0].manifest_key != lane_manifest_key(INTRADAY_MANIFEST)
        or len(scopes) != 1
        or scopes[0].scope != _ORB_SCOPE
        or scopes[0].scope_key != _ORB_SCOPE_KEY
    ):
        raise InvalidLifecycleControllerSourceError


def _require_exact_experiment_lineage(
    experiment_ledger: ExperimentLedgerStore,
    expected_strategy_version: str,
) -> StrategyVersionRegistration:
    if not experiment_ledger.is_initialized():
        raise InvalidLifecycleControllerSourceError
    hypotheses = tuple(
        stored
        for stored in experiment_ledger.hypotheses()
        if stored.registration.hypothesis_id == _ORB_CONTRACT.hypothesis_id
    )
    versions = tuple(
        stored
        for stored in experiment_ledger.strategy_versions()
        if stored.registration.strategy_version == expected_strategy_version
    )
    if len(hypotheses) != 1 or len(versions) != 1:
        raise InvalidLifecycleControllerSourceError
    hypothesis = hypotheses[0].registration
    version = versions[0].registration
    if (
        hypothesis.experiment_scope != _ORB_SCOPE
        or hypothesis.experiment_scope_key != _ORB_SCOPE_KEY
        or hypothesis.primary_lane is not LaneId.INTRADAY_MOMENTUM
        or hypothesis.hypothesis != _ORB_CONTRACT.hypothesis
        or hypothesis.falsification_rule != _ORB_CONTRACT.falsification_rule
        or version.strategy_id != StrategyMode.ORB.value
        or version.strategy_version != expected_strategy_version
        or (
            version.strategy_version != _ORB_CONTRACT.strategy_version
            and version.strategy_version != strategy_version_identity(StrategyMode.ORB, version.code_version)
        )
        or version.hypothesis_id != _ORB_CONTRACT.hypothesis_id
        or version.experiment_scope_key != _ORB_SCOPE_KEY
        or version.lane_id is not LaneId.INTRADAY_MOMENTUM
        or version.parameter_set != _ORB_CONTRACT.parameter_set
        or version.data_contract != CURRENT_DATA_CONTRACT
        or version.cost_model != CURRENT_COST_MODEL
        or version.portfolio_policy != SHADOW_PORTFOLIO_POLICY
        or version.source_registered_at != _ORB_SCOPE.registered_at
    ):
        raise InvalidLifecycleControllerSourceError
    return version


def _existing_controller_replay(
    source: _VerifiedControllerSource,
    session_date: dt.date,
) -> LifecycleControllerResult | None:
    matches = tuple(
        (index, stored)
        for index, stored in enumerate(source.lifecycle_events)
        if stored.event.policy_version == CURRENT_LIFECYCLE_CONTROLLER_POLICY
        and stored.event.decision_session_date == session_date
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise InvalidLifecycleControllerSourceError
    index, stored = matches[0]
    if index == 0:
        raise InvalidLifecycleControllerSourceError
    previous = source.lifecycle_events[index - 1]
    event = stored.event
    expected_evidence = tuple(sorted((str(previous.event_key), source.review_key, source.snapshot_key)))
    if (
        event.event_kind is not StrategyLifecycleEventKind.TRANSITION
        or event.from_state is not previous.event.to_state
        or event.to_state is not StrategyLifecycleState.SUSPENDED
        or event.previous_event_key != previous.event_key
        or event.evidence_keys != expected_evidence
        or event.reason_codes != _SUSPEND_REASON_CODES
        or event.effective_session_date != _next_regular_session(session_date)
        or event.decided_at < source.review.reviewed_at
    ):
        raise InvalidLifecycleControllerSourceError
    return LifecycleControllerResult(
        outcome=LifecycleControllerOutcome.TRANSITIONED,
        created=False,
        session_date=session_date,
        from_state=previous.event.to_state,
        to_state=StrategyLifecycleState.SUSPENDED,
        reason_codes=_SUSPEND_REASON_CODES,
        blockers=(),
        event=event,
    )


def _closed_policy_decision(
    source: _VerifiedControllerSource,
    session_date: dt.date,
) -> LifecycleControllerResult | None:
    state = source.current.event.to_state
    if state is StrategyLifecycleState.SUSPENDED:
        return _non_transition_result(
            LifecycleControllerOutcome.NO_CHANGE,
            session_date,
            state,
            ("already_suspended",),
        )
    if state is StrategyLifecycleState.REJECTED:
        return _non_transition_result(
            LifecycleControllerOutcome.NO_CHANGE,
            session_date,
            state,
            ("rejected_terminal",),
        )
    action = source.review.adaptive_action
    if action in {AdaptiveAction.COLLECTING, AdaptiveAction.SHADOW_CONTINUE}:
        return _non_transition_result(
            LifecycleControllerOutcome.NO_CHANGE,
            session_date,
            state,
            (),
        )
    if action is AdaptiveAction.DIAGNOSE:
        return _non_transition_result(
            LifecycleControllerOutcome.NO_CHANGE,
            session_date,
            state,
            ("diagnosis_required",),
        )
    if action is AdaptiveAction.EARLY_STOP:
        return _non_transition_result(
            LifecycleControllerOutcome.BLOCKED,
            session_date,
            state,
            ("early_stop_rejection_not_enabled",),
        )
    if action is AdaptiveAction.COMPARISON_READY:
        return _non_transition_result(
            LifecycleControllerOutcome.BLOCKED,
            session_date,
            state,
            ("equal_risk_terminal_trial_evidence_missing",),
        )
    if action is AdaptiveAction.PROMOTION_REVIEW:
        return _non_transition_result(
            LifecycleControllerOutcome.BLOCKED,
            session_date,
            state,
            PROMOTION_BLOCKERS,
        )
    if (
        action is not AdaptiveAction.SUSPEND
        or source.review.reviewer_action is not LaneReviewerAction.STOP_RECOMMENDED
        or "five_day_clear_degradation" not in source.review.reasons
    ):
        raise InvalidLifecycleControllerSourceError
    if not source.snapshot.data_quality_complete or source.snapshot.incidents:
        return _non_transition_result(
            LifecycleControllerOutcome.BLOCKED,
            session_date,
            state,
            ("clean_finalized_snapshot_required",),
        )
    if state not in _SUSPENDIBLE_STATES:
        return _non_transition_result(
            LifecycleControllerOutcome.BLOCKED,
            session_date,
            state,
            ("current_state_not_suspendible",),
        )
    return None


def _suspend_event(
    source: _VerifiedControllerSource,
    session_date: dt.date,
    decided_at: dt.datetime,
) -> StrategyLifecycleEvent:
    latest = source.lifecycle_events[-1]
    return StrategyLifecycleEvent(
        strategy_version=latest.event.strategy_version,
        sequence=latest.event.sequence + 1,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=latest.event.to_state,
        to_state=StrategyLifecycleState.SUSPENDED,
        policy_version=CURRENT_LIFECYCLE_CONTROLLER_POLICY,
        decision_session_date=session_date,
        effective_session_date=_next_regular_session(session_date),
        decided_at=decided_at,
        evidence_keys=tuple(sorted((str(latest.event_key), source.review_key, source.snapshot_key))),
        reason_codes=_SUSPEND_REASON_CODES,
        previous_event_key=latest.event_key,
    )


def _non_transition_result(
    outcome: LifecycleControllerOutcome,
    session_date: dt.date,
    from_state: StrategyLifecycleState,
    blockers: tuple[str, ...],
) -> LifecycleControllerResult:
    return LifecycleControllerResult(
        outcome=outcome,
        created=False,
        session_date=session_date,
        from_state=from_state,
        to_state=None,
        reason_codes=("review_evidence_verified",),
        blockers=blockers,
        event=None,
    )


def _expected_reviewer_action(action: AdaptiveAction) -> LaneReviewerAction:
    return {
        AdaptiveAction.COLLECTING: LaneReviewerAction.CONTINUE_COLLECTION,
        AdaptiveAction.SHADOW_CONTINUE: LaneReviewerAction.CONTINUE_COLLECTION,
        AdaptiveAction.EARLY_STOP: LaneReviewerAction.STOP_RECOMMENDED,
        AdaptiveAction.DIAGNOSE: LaneReviewerAction.DIAGNOSIS_REQUIRED,
        AdaptiveAction.COMPARISON_READY: LaneReviewerAction.COMPARISON_READY,
        AdaptiveAction.SUSPEND: LaneReviewerAction.STOP_RECOMMENDED,
        AdaptiveAction.PROMOTION_REVIEW: LaneReviewerAction.PROMOTION_REVIEW_BLOCKED,
    }[action]


def _next_regular_session(session_date: dt.date) -> dt.date:
    for offset in range(1, 11):
        candidate = session_date + dt.timedelta(days=offset)
        if regular_session_bounds(candidate) is not None:
            return candidate
    raise InvalidLifecycleControllerSourceError


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
