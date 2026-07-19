from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.kr_theme_day_review_models import KrThemeDayReviewAction
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent


class KrThemeDayLifecycleOutcome(StrEnum):
    REGISTERED = "registered"
    NO_CHANGE = "no_change"
    TRANSITIONED = "transitioned"


@dataclass(frozen=True, slots=True)
class KrThemeDayLifecycleDecision:
    target_state: StrategyLifecycleState | None
    reason_codes: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KrThemeDayLifecycleResult:
    outcome: KrThemeDayLifecycleOutcome
    created: bool
    from_state: StrategyLifecycleState | None
    to_state: StrategyLifecycleState | None
    reason_codes: tuple[str, ...]
    blockers: tuple[str, ...]
    event: MultiMarketStrategyLifecycleEvent | None


def decide_kr_theme_day_lifecycle(
    current_state: StrategyLifecycleState,
    review_action: KrThemeDayReviewAction,
) -> KrThemeDayLifecycleDecision:
    match review_action:
        case KrThemeDayReviewAction.CONTINUE_COLLECTION:
            return KrThemeDayLifecycleDecision(None, ("forward_evidence_collecting",), ())
        case KrThemeDayReviewAction.DATA_QUALITY_REVIEW:
            if current_state in {
                StrategyLifecycleState.EXPERIMENTAL_SHADOW,
                StrategyLifecycleState.CHALLENGER,
                StrategyLifecycleState.SHADOW_CHAMPION,
            }:
                return KrThemeDayLifecycleDecision(
                    StrategyLifecycleState.SUSPENDED,
                    ("data_quality_review_required", "review_evidence_verified"),
                    (),
                )
            return KrThemeDayLifecycleDecision(None, ("data_quality_review_required",), ())
        case KrThemeDayReviewAction.COMPARISON_READY:
            blockers = (
                "allocation_change_forbidden",
                "independent_comparator_missing",
                "multiple_testing_evidence_missing",
                "paper_authority_forbidden",
                "shadow_champion_forbidden",
            )
            if current_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW:
                return KrThemeDayLifecycleDecision(
                    StrategyLifecycleState.CHALLENGER,
                    ("minimum_forward_evidence_satisfied", "review_evidence_verified"),
                    blockers,
                )
            return KrThemeDayLifecycleDecision(None, ("comparison_evidence_already_projected",), blockers)
        case unreachable:
            assert_never(unreachable)
