from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.dashboard_authority_adapters import ProductionTriggerAuthorityResolver
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture
from trading_agent.dashboard_trigger_authority import (
    PersistedTriggerAuthorityResolver,
    TriggerAuthorityStore,
    authority_record_for,
)
from trading_agent.experiment_ledger_keys import experiment_trial_event_key
from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    StoredExperimentTrialEvent,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lane_review_models import LaneReviewerAction, LaneReviewEvent
from trading_agent.lane_review_store import LaneReviewReader, StoredLaneReviewEvent


class _ExperimentReader(ExperimentLedgerReader):
    def __init__(self, path: Path, event: StoredExperimentTrialEvent) -> None:
        super().__init__(path)
        self._event = event

    def trial_events(self, trial_id: str) -> tuple[StoredExperimentTrialEvent, ...]:
        return (self._event,) if trial_id == self._event.event.trial_id else ()


class _ReviewReader(LaneReviewReader):
    def __init__(self, path: Path, event: StoredLaneReviewEvent) -> None:
        super().__init__(path)
        self._event = event

    def events(self) -> tuple[StoredLaneReviewEvent, ...]:
        return (self._event,)


def test_persisted_source_authority_requires_exact_payload_and_time(tmp_path: Path) -> None:
    # Given: one exact immutable source authority record
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    trigger = AutonomousTriggerV1.model_validate(trigger_fixture(now=now))
    store = TriggerAuthorityStore(tmp_path / "authorities")
    assert store.append(authority_record_for(trigger))
    resolver = PersistedTriggerAuthorityResolver(store)

    # When / Then: exact source authority passes while a self-asserted payload does not
    assert resolver.blocker(trigger, now) is None
    forged = trigger.model_copy(update={"payload_sha256": "0" * 64})
    assert resolver.blocker(forged, now) == "source_authority_invalid"


def test_experiment_result_resolves_terminal_event_from_experiment_ledger(tmp_path: Path) -> None:
    # Given: one terminal experiment event exposed only through the query reader
    occurred = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    event = ExperimentTrialEvent(
        trial_id="trial-authority-001",
        sequence=1,
        event_kind=TrialEventKind.CENSORED,
        occurred_at=occurred,
        artifact_sha256s=(),
        reason_codes=("fixture_terminal",),
        previous_event_key=None,
    )
    stored = StoredExperimentTrialEvent(experiment_trial_event_key(event), event)
    trigger = _trigger_for_event(
        occurred,
        trigger_type="experiment_result",
        authority="experiment_ledger",
        source_id=event.trial_id,
        evidence_ref=str(stored.event_key),
    )
    resolver = ProductionTriggerAuthorityResolver(
        persisted=PersistedTriggerAuthorityResolver(TriggerAuthorityStore(tmp_path / "authorities")),
        experiments=_ExperimentReader(tmp_path / "experiments.sqlite3", stored),
        reviews=LaneReviewReader(tmp_path / "reviews.sqlite3"),
    )

    # When / Then: exact ledger identity passes and a forged evidence hash fails
    assert resolver.blocker(trigger, occurred) is None
    forged = trigger.model_copy(update={"payload_sha256": "0" * 64})
    assert resolver.blocker(forged, occurred) == "experiment_authority_mismatch"


def test_reviewer_feedback_resolves_lane_review_event(tmp_path: Path) -> None:
    # Given: one accepted comparison-ready lane review in its query reader
    occurred = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    review = LaneReviewEvent(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=dt.date(2026, 7, 25),
        snapshot_key="a" * 64,
        experiment_scope_key="b" * 64,
        daily_record_id="c" * 64,
        daily_record_sha256="d" * 64,
        adaptive_evaluation_sha256="e" * 64,
        strategy_version="strategy-v1",
        evaluator_version="evaluator-v1",
        reviewer_version="lane_reviewer_v1",
        adaptive_action=AdaptiveAction.COMPARISON_READY,
        reviewer_action=LaneReviewerAction.COMPARISON_READY,
        reasons=("comparison_ready",),
        blockers=(),
        reviewed_at=occurred,
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
    )
    stored = StoredLaneReviewEvent(lane_review_event_key(review), review)
    trigger = _trigger_for_event(
        occurred,
        trigger_type="reviewer_feedback",
        authority="independent_reviewer",
        source_id=review.snapshot_key,
        evidence_ref=str(stored.event_key),
    )
    resolver = ProductionTriggerAuthorityResolver(
        persisted=PersistedTriggerAuthorityResolver(TriggerAuthorityStore(tmp_path / "authorities")),
        experiments=ExperimentLedgerReader(tmp_path / "experiments.sqlite3"),
        reviews=_ReviewReader(tmp_path / "reviews.sqlite3", stored),
    )

    # When / Then: exact persisted reviewer authority passes
    assert resolver.blocker(trigger, occurred) is None


def _trigger_for_event(
    occurred: dt.datetime,
    *,
    trigger_type: str,
    authority: str,
    source_id: str,
    evidence_ref: str,
) -> AutonomousTriggerV1:
    payload = trigger_fixture(now=occurred)
    payload.update(
        {
            "trigger_type": trigger_type,
            "authority": authority,
            "source_receipt_ids": (source_id,),
            "evidence_refs": (evidence_ref,),
            "payload_sha256": evidence_ref,
        }
    )
    return AutonomousTriggerV1.model_validate(payload)
