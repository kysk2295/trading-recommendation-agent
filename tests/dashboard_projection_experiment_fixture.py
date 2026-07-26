from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_experiment_ledger_store import (
    _lifecycle_registration,
    _research_card,
    _research_source,
    _started_event,
    _strategy_authority_binding,
    _terminal_event,
    _trial,
    _version,
)
from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.experiment_ledger_keys import (
    experiment_trial_event_key,
    strategy_authority_binding_key,
    strategy_lifecycle_event_key,
)
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.lane_policy_models import LaneId as ReviewLaneId
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lane_review_models import LaneReviewerAction, LaneReviewEvent
from trading_agent.lane_review_store import LaneReviewStore
from trading_agent.research_identity_models import AgentOperatingMode


def complete_experiment_outputs(tmp_path: Path, *, strict_stage_times: bool = True) -> Path:
    outputs = tmp_path / "outputs"
    ledger = ExperimentLedgerStore(outputs / "experiment_control" / "experiment_ledger.sqlite3")
    started = _started_event()
    card = _research_card()
    version = _version()
    trial = _trial()
    if strict_stage_times:
        card = card.model_copy(
            update={
                "hypothesis": card.hypothesis.model_copy(
                    update={"ledger_recorded_at": dt.datetime(2026, 7, 15, 12, tzinfo=dt.UTC)}
                )
            }
        )
        version = version.model_copy(update={"ledger_recorded_at": dt.datetime(2026, 7, 15, 13, tzinfo=dt.UTC)})
        trial = trial.model_copy(update={"registered_at": dt.datetime(2026, 7, 15, 14, tzinfo=dt.UTC)})
    with ledger.writer() as writer:
        assert writer.register_research_source(_research_source())
        assert writer.register_research_hypothesis(card)
        assert writer.register_strategy_version(version)
        assert writer.register_trial(trial)
        assert writer.append_trial_event(started)
        assert writer.append_trial_event(_terminal_event(started))
    return outputs


def append_reviewer_and_lifecycle(
    outputs: Path,
    *,
    snapshot_key: str | None = None,
    review_lane: ReviewLaneId | None = None,
) -> None:
    completed = _terminal_event(_started_event())
    review = LaneReviewEvent(
        lane_id=ReviewLaneId(_version().lane_id.value) if review_lane is None else review_lane,
        session_date=dt.date(2026, 7, 17),
        snapshot_key=snapshot_key or str(experiment_trial_event_key(completed)),
        experiment_scope_key=_version().experiment_scope_key,
        daily_record_id="a" * 64,
        daily_record_sha256="b" * 64,
        adaptive_evaluation_sha256="c" * 64,
        strategy_version=_version().strategy_version,
        evaluator_version="evaluator-v1",
        reviewer_version="lane_reviewer_v1",
        adaptive_action=AdaptiveAction.COMPARISON_READY,
        reviewer_action=LaneReviewerAction.COMPARISON_READY,
        reasons=("comparison_ready",),
        blockers=(),
        reviewed_at=dt.datetime(2026, 7, 17, 19, tzinfo=dt.UTC),
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
    )
    review_store = LaneReviewStore(outputs / "lane_control" / "lane_review.sqlite3")
    with review_store.writer() as writer:
        assert writer.append_event(review)
    binding = _strategy_authority_binding(AgentOperatingMode.SHADOW).model_copy(
        update={"bound_at": dt.datetime(2026, 7, 15, 15, tzinfo=dt.UTC)}
    )
    reader = ExperimentLedgerReader(outputs / "experiment_control" / "experiment_ledger.sqlite3")
    stored_hypothesis = reader.hypotheses()[0]
    stored_version = reader.strategy_versions()[0]
    registration = _lifecycle_registration().model_copy(
        update={
            "evidence_keys": tuple(
                sorted(
                    (
                        str(stored_hypothesis.registration_key),
                        stored_version.registration.experiment_scope_key,
                        str(stored_version.registration_key),
                    )
                )
            )
        }
    )
    challenger = StrategyLifecycleEvent(
        strategy_version=registration.strategy_version,
        sequence=2,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=registration.to_state,
        to_state=StrategyLifecycleState.CHALLENGER,
        policy_version=registration.policy_version,
        decision_session_date=dt.date(2026, 7, 16),
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
        evidence_keys=("d" * 64,),
        reason_codes=("review_evidence_verified",),
        previous_event_key=str(strategy_lifecycle_event_key(registration)),
    )
    champion = StrategyLifecycleEvent(
        strategy_version=registration.strategy_version,
        sequence=3,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=challenger.to_state,
        to_state=StrategyLifecycleState.SHADOW_CHAMPION,
        policy_version=registration.policy_version,
        decision_session_date=dt.date(2026, 7, 17),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 17, 20, tzinfo=dt.UTC),
        evidence_keys=tuple(
            sorted(
                (
                    str(experiment_trial_event_key(completed)),
                    str(strategy_authority_binding_key(binding)),
                    str(lane_review_event_key(review)),
                )
            )
        ),
        reason_codes=("review_evidence_verified",),
        previous_event_key=str(strategy_lifecycle_event_key(challenger)),
    )
    ledger = ExperimentLedgerStore(outputs / "experiment_control" / "experiment_ledger.sqlite3")
    with ledger.writer() as writer:
        assert writer.register_strategy_authority_binding(binding)
        assert writer.append_lifecycle_event(registration)
        assert writer.append_lifecycle_event(challenger)
        assert writer.append_lifecycle_event(champion)
