from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.dashboard_reviewer_lifecycle import ReviewerLifecycleAuthorityReader
from trading_agent.experiment_ledger_keys import (
    StrategyLifecycleEventKey,
    strategy_authority_binding_key,
)
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    StoredStrategyAuthorityBinding,
    StoredStrategyLifecycleEvent,
)
from trading_agent.lane_identity_models import LaneId
from trading_agent.lane_policy_models import LaneId as ReviewLaneId
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lane_review_models import LaneReviewerAction, LaneReviewEvent
from trading_agent.lane_review_store import LaneReviewReader, StoredLaneReviewEvent
from trading_agent.research_identity_models import AgentFamily, AgentOperatingMode, MarketId, StrategyLaneRef
from trading_agent.strategy_authority_models import StrategyAuthorityBinding


class _ExperimentReader(ExperimentLedgerReader):
    def __init__(
        self,
        path: Path,
        bindings: tuple[StoredStrategyAuthorityBinding, ...],
        events: dict[str, tuple[StoredStrategyLifecycleEvent, ...]],
    ) -> None:
        super().__init__(path)
        self._bindings = bindings
        self._events = events

    def strategy_authority_bindings(self) -> tuple[StoredStrategyAuthorityBinding, ...]:
        return self._bindings

    def lifecycle_events(self, strategy_version: str) -> tuple[StoredStrategyLifecycleEvent, ...]:
        return self._events.get(strategy_version, ())


class _ReviewReader(LaneReviewReader):
    def __init__(self, path: Path, events: tuple[StoredLaneReviewEvent, ...]) -> None:
        super().__init__(path)
        self._events = events

    def events(self) -> tuple[StoredLaneReviewEvent, ...]:
        return self._events


def test_query_only_authority_rejects_candidate_absent_from_persisted_stores(tmp_path: Path) -> None:
    # Given: empty query-only ExperimentLedger and lane Reviewer readers
    authority = ReviewerLifecycleAuthorityReader(
        experiments=(ExperimentLedgerReader(tmp_path / "experiments.sqlite3"),),
        reviews=(LaneReviewReader(tmp_path / "reviews.sqlite3"),),
    )

    # When / Then: caller knowledge alone cannot authorize promotion or allocation
    assert not authority.promotion_is_authorized("a" * 64)
    assert not authority.allocation_manager_is_available()


def test_query_only_authority_requires_two_independent_persisted_champions(tmp_path: Path) -> None:
    # Given: two distinct persisted lifecycle chains with accepted lane reviews
    first = _authority_chain("strategy-day-v1", AgentFamily.DAY_TRADING, LaneId.INTRADAY_MOMENTUM, "a")
    second = _authority_chain("strategy-swing-v1", AgentFamily.SWING_TRADING, LaneId.SWING_MOMENTUM, "b")
    experiment = _ExperimentReader(
        tmp_path / "experiments.sqlite3",
        (first[0], second[0]),
        {first[0].binding.strategy_version: (first[1],), second[0].binding.strategy_version: (second[1],)},
    )
    reviews = _ReviewReader(tmp_path / "reviews.sqlite3", (first[2], second[2]))

    # When: allocation authority queries the persisted reviewers and lifecycle events
    authority = ReviewerLifecycleAuthorityReader(experiments=(experiment,), reviews=(reviews,))

    # Then: one remains locked while two independent champion versions unlock allocation
    single = ReviewerLifecycleAuthorityReader(
        experiments=(
            _ExperimentReader(
                tmp_path / "single.sqlite3",
                (first[0],),
                {first[0].binding.strategy_version: (first[1],)},
            ),
        ),
        reviews=(_ReviewReader(tmp_path / "single-review.sqlite3", (first[2],)),),
    )
    assert not single.allocation_manager_is_available()
    assert authority.allocation_manager_is_available()
    assert authority.promotion_is_authorized("a" * 64)


def test_allocation_authority_does_not_count_two_versions_of_one_family_lane(tmp_path: Path) -> None:
    first = _authority_chain("strategy-day-v1", AgentFamily.DAY_TRADING, LaneId.INTRADAY_MOMENTUM, "a")
    second = _authority_chain("strategy-day-v2", AgentFamily.DAY_TRADING, LaneId.INTRADAY_MOMENTUM, "b")
    authority = ReviewerLifecycleAuthorityReader(
        experiments=(
            _ExperimentReader(
                tmp_path / "experiments.sqlite3",
                (first[0], second[0]),
                {first[0].binding.strategy_version: (first[1],), second[0].binding.strategy_version: (second[1],)},
            ),
        ),
        reviews=(_ReviewReader(tmp_path / "reviews.sqlite3", (first[2], second[2])),),
    )

    assert not authority.allocation_manager_is_available()


def _authority_chain(
    version: str,
    family: AgentFamily,
    lane: LaneId,
    candidate: str,
) -> tuple[StoredStrategyAuthorityBinding, StoredStrategyLifecycleEvent, StoredLaneReviewEvent]:
    decided = dt.datetime(2026, 7, 26, 20, tzinfo=dt.UTC)
    binding = StrategyAuthorityBinding(
        strategy_version=version,
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=family,
            strategy_id=version.replace("-", "_"),
        ),
        operating_mode=AgentOperatingMode.SHADOW,
        legacy_lane_id=lane,
        bound_at=decided - dt.timedelta(days=2),
    )
    stored_binding = StoredStrategyAuthorityBinding(strategy_authority_binding_key(binding), binding)
    review = LaneReviewEvent(
        lane_id=ReviewLaneId(lane.value),
        session_date=dt.date(2026, 7, 25),
        snapshot_key=candidate * 64,
        experiment_scope_key="c" * 64,
        daily_record_id="d" * 64,
        daily_record_sha256="e" * 64,
        adaptive_evaluation_sha256="f" * 64,
        strategy_version=version,
        evaluator_version="evaluator-v1",
        reviewer_version="lane_reviewer_v1",
        adaptive_action=AdaptiveAction.COMPARISON_READY,
        reviewer_action=LaneReviewerAction.COMPARISON_READY,
        reasons=("comparison_ready",),
        blockers=(),
        reviewed_at=decided - dt.timedelta(hours=1),
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
    )
    stored_review = StoredLaneReviewEvent(lane_review_event_key(review), review)
    evidence = tuple(sorted((candidate * 64, str(stored_binding.binding_key), str(stored_review.event_key))))
    lifecycle = StrategyLifecycleEvent(
        strategy_version=version,
        sequence=2,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=StrategyLifecycleState.CHALLENGER,
        to_state=StrategyLifecycleState.SHADOW_CHAMPION,
        policy_version="lifecycle-v1",
        decision_session_date=dt.date(2026, 7, 26),
        effective_session_date=dt.date(2026, 7, 27),
        decided_at=decided,
        evidence_keys=evidence,
        reason_codes=("review_evidence_verified",),
        previous_event_key="9" * 64,
    )
    stored_lifecycle = StoredStrategyLifecycleEvent(StrategyLifecycleEventKey("8" * 64), lifecycle)
    return stored_binding, stored_lifecycle, stored_review
