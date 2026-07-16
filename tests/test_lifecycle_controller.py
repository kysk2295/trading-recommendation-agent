from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.daily_research_contract import (
    EVALUATOR_VERSION,
    strategy_contract,
    strategy_version_identity,
)
from trading_agent.experiment_ledger_bootstrap import bootstrap_current_intraday_experiments
from trading_agent.experiment_ledger_keys import strategy_lifecycle_event_key
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_daily_snapshot_key,
    lane_manifest_key,
)
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
    INTRADAY_MANIFEST,
    current_intraday_experiment_scope,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lane_review_models import (
    CURRENT_LANE_REVIEWER_VERSION,
    LaneReviewerAction,
    LaneReviewEvent,
)
from trading_agent.lane_review_store import LaneReviewStore
from trading_agent.lifecycle_controller import (
    CURRENT_LIFECYCLE_CONTROLLER_POLICY,
    PROMOTION_BLOCKERS,
    InvalidLifecycleControllerSourceError,
    LifecycleControllerOutcome,
    control_intraday_orb_lifecycle,
)
from trading_agent.strategy_factory import StrategyMode

SESSION_DATE = dt.date(2026, 7, 15)
NEXT_SESSION_DATE = dt.date(2026, 7, 16)
BOOTSTRAP_AT = dt.datetime(2026, 7, 14, 20, tzinfo=dt.UTC)
FINALIZED_AT = dt.datetime(2026, 7, 15, 20, 10, tzinfo=dt.UTC)
REVIEWED_AT = dt.datetime(2026, 7, 15, 20, 20, tzinfo=dt.UTC)
DECIDED_AT = dt.datetime(2026, 7, 15, 20, 30, tzinfo=dt.UTC)
ORB_CONTRACT = strategy_contract(StrategyMode.ORB)
CODE_VERSION = "test-code"
ORB_VERSION = strategy_version_identity(StrategyMode.ORB, CODE_VERSION)
ORB_SCOPE = current_intraday_experiment_scope(ORB_CONTRACT.hypothesis_id)
ORB_SCOPE_KEY = experiment_scope_key(ORB_SCOPE)


@dataclass(frozen=True, slots=True)
class _Sources:
    lanes: LaneRegistryStore
    reviews: LaneReviewStore
    experiments: ExperimentLedgerStore
    snapshot: LaneDailySnapshot
    review: LaneReviewEvent


def _reviewer_action(action: AdaptiveAction) -> LaneReviewerAction:
    return {
        AdaptiveAction.COLLECTING: LaneReviewerAction.CONTINUE_COLLECTION,
        AdaptiveAction.SHADOW_CONTINUE: LaneReviewerAction.CONTINUE_COLLECTION,
        AdaptiveAction.EARLY_STOP: LaneReviewerAction.STOP_RECOMMENDED,
        AdaptiveAction.DIAGNOSE: LaneReviewerAction.DIAGNOSIS_REQUIRED,
        AdaptiveAction.COMPARISON_READY: LaneReviewerAction.COMPARISON_READY,
        AdaptiveAction.SUSPEND: LaneReviewerAction.STOP_RECOMMENDED,
        AdaptiveAction.PROMOTION_REVIEW: LaneReviewerAction.PROMOTION_REVIEW_BLOCKED,
    }[action]


def _default_reasons(action: AdaptiveAction) -> tuple[str, ...]:
    return {
        AdaptiveAction.COLLECTING: ("minimum_five_day_observation_pending",),
        AdaptiveAction.SHADOW_CONTINUE: ("shadow_evidence_stable",),
        AdaptiveAction.EARLY_STOP: ("five_day_early_stop",),
        AdaptiveAction.DIAGNOSE: ("ten_day_diagnosis_required",),
        AdaptiveAction.COMPARISON_READY: ("twenty_day_comparison_ready",),
        AdaptiveAction.SUSPEND: ("five_day_clear_degradation",),
        AdaptiveAction.PROMOTION_REVIEW: ("sixty_day_promotion_review",),
    }[action]


def _seed_base_sources(tmp_path: Path) -> tuple[LaneRegistryStore, LaneReviewStore, ExperimentLedgerStore]:
    lanes = LaneRegistryStore(tmp_path / "lane.sqlite3")
    with lanes.writer() as writer:
        for manifest in DEFAULT_LANE_MANIFESTS:
            _ = writer.register_manifest(manifest)
        for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES:
            _ = writer.register_experiment_scope(scope)
    experiments = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = bootstrap_current_intraday_experiments(
        lane_registry=lanes,
        experiment_ledger=experiments,
        code_version=CODE_VERSION,
        recorded_at=BOOTSTRAP_AT,
    )
    return lanes, LaneReviewStore(tmp_path / "review.sqlite3"), experiments


def _append_day_sources(
    lanes: LaneRegistryStore,
    reviews: LaneReviewStore,
    *,
    session_date: dt.date,
    action: AdaptiveAction,
    finalized_at: dt.datetime,
    reviewed_at: dt.datetime,
    data_quality_complete: bool = True,
    incidents: tuple[str, ...] = (),
    review_changes: dict[str, object] | None = None,
) -> tuple[LaneDailySnapshot, LaneReviewEvent]:
    snapshot = LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=session_date,
        finalized_at=finalized_at,
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(ORB_SCOPE_KEY,),
        source_ledger_generation=1,
        source_ledger_sha256="a" * 64,
        champion_strategy_versions=(),
        data_quality_complete=data_quality_complete,
        allocation_eligible=False,
        incidents=incidents,
        conservative_equity=Decimal("30000"),
        realized_pnl=Decimal(0),
        unrealized_pnl=Decimal(0),
        planned_open_risk=Decimal(0),
        open_order_count=0,
        open_position_count=0,
    )
    with lanes.writer() as writer:
        assert writer.append_daily_snapshot(snapshot) is True
    event_data: dict[str, object] = {
        "lane_id": LaneId.INTRADAY_MOMENTUM,
        "session_date": session_date,
        "snapshot_key": lane_daily_snapshot_key(snapshot),
        "experiment_scope_key": ORB_SCOPE_KEY,
        "daily_record_id": "b" * 64,
        "daily_record_sha256": "c" * 64,
        "adaptive_evaluation_sha256": "d" * 64,
        "strategy_version": ORB_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "reviewer_version": CURRENT_LANE_REVIEWER_VERSION,
        "adaptive_action": action,
        "reviewer_action": _reviewer_action(action),
        "reasons": _default_reasons(action),
        "blockers": ("allocation_ineligible", "champion_missing"),
        "reviewed_at": reviewed_at,
        "automatic_state_change_allowed": False,
        "order_authority_change_allowed": False,
    }
    if review_changes is not None:
        event_data.update(review_changes)
    review = LaneReviewEvent.model_validate(event_data)
    with reviews.writer() as writer:
        assert writer.append_event(review) is True
    return snapshot, review


def _sources(
    tmp_path: Path,
    *,
    action: AdaptiveAction = AdaptiveAction.SUSPEND,
    data_quality_complete: bool = True,
    incidents: tuple[str, ...] = (),
    review_changes: dict[str, object] | None = None,
) -> _Sources:
    lanes, reviews, experiments = _seed_base_sources(tmp_path)
    snapshot, review = _append_day_sources(
        lanes,
        reviews,
        session_date=SESSION_DATE,
        action=action,
        finalized_at=FINALIZED_AT,
        reviewed_at=REVIEWED_AT,
        data_quality_complete=data_quality_complete,
        incidents=incidents,
        review_changes=review_changes,
    )
    return _Sources(lanes, reviews, experiments, snapshot, review)


def _control(
    sources: _Sources,
    *,
    session_date: dt.date = SESSION_DATE,
    decided_at: dt.datetime = DECIDED_AT,
):
    return control_intraday_orb_lifecycle(
        lane_registry=sources.lanes,
        review_ledger=sources.reviews,
        experiment_ledger=sources.experiments,
        session_date=session_date,
        decided_at=decided_at,
    )


def test_controller_appends_next_session_suspend_and_exact_replay(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    before = sources.experiments.lifecycle_events(ORB_VERSION)[0]

    first = _control(sources)
    replay = _control(sources, decided_at=DECIDED_AT + dt.timedelta(minutes=5))

    assert first.outcome is LifecycleControllerOutcome.TRANSITIONED
    assert first.created is True
    assert first.from_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
    assert first.to_state is StrategyLifecycleState.SUSPENDED
    assert first.blockers == ()
    assert first.event is not None
    assert first.event.effective_session_date == NEXT_SESSION_DATE
    assert first.event.policy_version == CURRENT_LIFECYCLE_CONTROLLER_POLICY
    assert first.event.previous_event_key == before.event_key
    assert first.event.evidence_keys == tuple(
        sorted(
            (
                str(before.event_key),
                str(lane_daily_snapshot_key(sources.snapshot)),
                str(lane_review_event_key(sources.review)),
            )
        )
    )
    assert replay.outcome is LifecycleControllerOutcome.TRANSITIONED
    assert replay.created is False
    assert replay.event == first.event
    assert len(sources.experiments.lifecycle_events(ORB_VERSION)) == 2
    current = sources.experiments.lifecycle_state(ORB_VERSION, SESSION_DATE)
    effective = sources.experiments.lifecycle_state(
        ORB_VERSION,
        NEXT_SESSION_DATE,
    )
    assert current is not None
    assert current.event.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
    assert effective is not None
    assert effective.event.to_state is StrategyLifecycleState.SUSPENDED


@pytest.mark.parametrize(
    ("action", "outcome", "blockers"),
    (
        (AdaptiveAction.COLLECTING, LifecycleControllerOutcome.NO_CHANGE, ()),
        (AdaptiveAction.SHADOW_CONTINUE, LifecycleControllerOutcome.NO_CHANGE, ()),
        (
            AdaptiveAction.DIAGNOSE,
            LifecycleControllerOutcome.NO_CHANGE,
            ("diagnosis_required",),
        ),
        (
            AdaptiveAction.EARLY_STOP,
            LifecycleControllerOutcome.BLOCKED,
            ("early_stop_rejection_not_enabled",),
        ),
        (
            AdaptiveAction.COMPARISON_READY,
            LifecycleControllerOutcome.BLOCKED,
            ("equal_risk_terminal_trial_evidence_missing",),
        ),
        (
            AdaptiveAction.PROMOTION_REVIEW,
            LifecycleControllerOutcome.BLOCKED,
            PROMOTION_BLOCKERS,
        ),
    ),
)
def test_controller_closed_decision_table_does_not_append(
    tmp_path: Path,
    action: AdaptiveAction,
    outcome: LifecycleControllerOutcome,
    blockers: tuple[str, ...],
) -> None:
    sources = _sources(tmp_path, action=action)

    result = _control(sources)

    assert result.outcome is outcome
    assert result.created is False
    assert result.to_state is None
    assert result.blockers == blockers
    assert result.event is None
    assert len(sources.experiments.lifecycle_events(ORB_VERSION)) == 1


def test_controller_does_not_suspend_an_incomplete_or_incident_session(tmp_path: Path) -> None:
    sources = _sources(
        tmp_path,
        data_quality_complete=False,
        incidents=("data_quality_incomplete",),
    )

    result = _control(sources)

    assert result.outcome is LifecycleControllerOutcome.BLOCKED
    assert result.blockers == ("clean_finalized_snapshot_required",)
    assert result.event is None
    assert len(sources.experiments.lifecycle_events(ORB_VERSION)) == 1


@pytest.mark.parametrize(
    "review_changes",
    (
        {"reasons": ("other_suspend_reason",)},
        {"reviewer_action": LaneReviewerAction.CONTINUE_COLLECTION},
    ),
)
def test_controller_rejects_inconsistent_suspend_review(
    tmp_path: Path,
    review_changes: dict[str, object],
) -> None:
    sources = _sources(tmp_path, review_changes=review_changes)

    with pytest.raises(InvalidLifecycleControllerSourceError):
        _ = _control(sources)

    assert len(sources.experiments.lifecycle_events(ORB_VERSION)) == 1


@pytest.mark.parametrize(
    "review_changes",
    (
        {"snapshot_key": "e" * 64},
        {"experiment_scope_key": "e" * 64},
        {"strategy_version": "other_strategy_v1"},
        {"evaluator_version": "other_evaluator_v1"},
        {"session_date": dt.date(2026, 7, 14)},
    ),
)
def test_controller_rejects_mismatched_review_lineage(
    tmp_path: Path,
    review_changes: dict[str, object],
) -> None:
    sources = _sources(tmp_path, review_changes=review_changes)

    with pytest.raises(InvalidLifecycleControllerSourceError):
        _ = _control(sources)


def test_controller_rejects_invalid_decision_time_or_review_order(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    late_review_sources = _sources(
        tmp_path / "late",
        review_changes={"reviewed_at": DECIDED_AT + dt.timedelta(seconds=1)},
    )

    with pytest.raises(InvalidLifecycleControllerSourceError):
        _ = _control(sources, decided_at=dt.datetime(2026, 7, 15, 20, 30))
    with pytest.raises(InvalidLifecycleControllerSourceError):
        _ = _control(late_review_sources)


def test_controller_rejects_a_future_effective_pending_event(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    previous = sources.experiments.lifecycle_events(ORB_VERSION)[-1]
    pending = StrategyLifecycleEvent(
        strategy_version=ORB_VERSION,
        sequence=previous.event.sequence + 1,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=previous.event.to_state,
        to_state=StrategyLifecycleState.EXPERIMENTAL_PAPER,
        policy_version="other_controller_v1",
        decision_session_date=SESSION_DATE,
        effective_session_date=NEXT_SESSION_DATE,
        decided_at=DECIDED_AT - dt.timedelta(minutes=1),
        evidence_keys=("e" * 64,),
        reason_codes=("other_evidence_verified",),
        previous_event_key=strategy_lifecycle_event_key(previous.event),
    )
    with sources.experiments.writer() as writer:
        assert writer.append_lifecycle_event(pending) is True

    with pytest.raises(InvalidLifecycleControllerSourceError):
        _ = _control(sources)

    assert len(sources.experiments.lifecycle_events(ORB_VERSION)) == 2


@pytest.mark.parametrize(
    ("terminal_state", "expected_blocker"),
    (
        (StrategyLifecycleState.SUSPENDED, "already_suspended"),
        (StrategyLifecycleState.REJECTED, "rejected_terminal"),
    ),
)
def test_controller_does_not_repeat_suspended_or_rejected_state(
    tmp_path: Path,
    terminal_state: StrategyLifecycleState,
    expected_blocker: str,
) -> None:
    lanes, reviews, experiments = _seed_base_sources(tmp_path)
    previous = experiments.lifecycle_events(ORB_VERSION)[-1]
    terminal = StrategyLifecycleEvent(
        strategy_version=ORB_VERSION,
        sequence=previous.event.sequence + 1,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=previous.event.to_state,
        to_state=terminal_state,
        policy_version="prior_controller_v1",
        decision_session_date=SESSION_DATE,
        effective_session_date=NEXT_SESSION_DATE,
        decided_at=DECIDED_AT,
        evidence_keys=("e" * 64,),
        reason_codes=("prior_evidence_verified",),
        previous_event_key=previous.event_key,
    )
    with experiments.writer() as writer:
        assert writer.append_lifecycle_event(terminal) is True
    next_finalized = dt.datetime(2026, 7, 16, 20, 10, tzinfo=dt.UTC)
    next_reviewed = dt.datetime(2026, 7, 16, 20, 20, tzinfo=dt.UTC)
    snapshot, review = _append_day_sources(
        lanes,
        reviews,
        session_date=NEXT_SESSION_DATE,
        action=AdaptiveAction.SUSPEND,
        finalized_at=next_finalized,
        reviewed_at=next_reviewed,
    )
    sources = _Sources(lanes, reviews, experiments, snapshot, review)

    result = _control(
        sources,
        session_date=NEXT_SESSION_DATE,
        decided_at=dt.datetime(2026, 7, 16, 20, 30, tzinfo=dt.UTC),
    )

    assert result.outcome is LifecycleControllerOutcome.NO_CHANGE
    assert result.blockers == (expected_blocker,)
    assert result.event is None
    assert len(experiments.lifecycle_events(ORB_VERSION)) == 2
