from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.lane_review_models import LaneReviewerAction
from trading_agent.lane_review_store import LaneReviewReader, StoredLaneReviewEvent
from trading_agent.research_identity_models import AgentFamily


@dataclass(frozen=True, slots=True)
class PersistedChampionAuthority:
    strategy_version: str
    family_id: AgentFamilyId
    lane_id: str
    lifecycle_ref: str
    reviewer_ref: str
    candidate_refs: tuple[str, ...]


class ReviewerLifecycleAuthorityReader:
    def __init__(
        self,
        *,
        experiments: tuple[ExperimentLedgerReader, ...],
        reviews: tuple[LaneReviewReader, ...],
    ) -> None:
        self._experiments = experiments
        self._reviews = reviews

    def promotion_is_authorized(self, candidate_ref: str) -> bool:
        return any(candidate_ref in champion.candidate_refs for champion in self.champions())

    def allocation_manager_is_available(self) -> bool:
        return len({(champion.family_id, champion.lane_id) for champion in self.champions()}) >= 2

    def champions(self) -> tuple[PersistedChampionAuthority, ...]:
        reviews = {
            str(stored.event_key): stored
            for reader in self._reviews
            for stored in reader.events()
            if stored.event.reviewer_action is LaneReviewerAction.COMPARISON_READY
        }
        champions: list[PersistedChampionAuthority] = []
        for ledger in self._experiments:
            for binding in ledger.strategy_authority_bindings():
                events = ledger.lifecycle_events(binding.binding.strategy_version)
                if not events:
                    continue
                lifecycle = events[-1]
                if lifecycle.event.to_state not in {
                    StrategyLifecycleState.SHADOW_CHAMPION,
                    StrategyLifecycleState.PAPER_CHAMPION,
                }:
                    continue
                review = _matching_review(lifecycle.event.evidence_keys, reviews, binding.binding.strategy_version)
                if review is None or lifecycle.event.decided_at < review.event.reviewed_at:
                    continue
                family_id = _family_id(binding.binding.strategy_lane.agent_family)
                if family_id is None:
                    continue
                reviewer_ref = str(review.event_key)
                candidates = tuple(
                    key for key in lifecycle.event.evidence_keys if key not in {reviewer_ref, str(binding.binding_key)}
                )
                champions.append(
                    PersistedChampionAuthority(
                        strategy_version=binding.binding.strategy_version,
                        family_id=family_id,
                        lane_id=binding.binding.legacy_lane_id.value,
                        lifecycle_ref=str(lifecycle.event_key),
                        reviewer_ref=reviewer_ref,
                        candidate_refs=candidates,
                    )
                )
        return tuple(champions)


def _matching_review(
    evidence_keys: tuple[str, ...],
    reviews: dict[str, StoredLaneReviewEvent],
    strategy_version: str,
) -> StoredLaneReviewEvent | None:
    matching = tuple(
        reviews[key]
        for key in evidence_keys
        if key in reviews
        and reviews[key].event.strategy_version == strategy_version
        and reviews[key].event.snapshot_key in evidence_keys
    )
    return matching[0] if len(matching) == 1 else None


def _family_id(family: AgentFamily) -> AgentFamilyId | None:
    match family:
        case AgentFamily.OPPORTUNITY_MANAGER:
            return "opportunity_manager"
        case AgentFamily.DAY_TRADING:
            return "day_trading"
        case AgentFamily.SWING_TRADING:
            return "swing_trading"
        case AgentFamily.SYSTEMATIC_QUANT:
            return "systematic_quant"
        case AgentFamily.MARKET_CONTEXT:
            return "market_context"
        case AgentFamily.ALLOCATION_MANAGER:
            return None
        case unreachable:
            assert_never(unreachable)


__all__ = ("PersistedChampionAuthority", "ReviewerLifecycleAuthorityReader")
