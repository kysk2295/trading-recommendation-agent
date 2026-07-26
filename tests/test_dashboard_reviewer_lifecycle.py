from __future__ import annotations

import datetime as dt

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_reviewer_lifecycle import (
    IndependentReviewerDecisionV1,
    LifecycleAuthorityDecisionV1,
    PersistedChampionAuthorityV1,
    allocation_manager_is_available,
    promotion_is_authorized,
)


def test_candidate_cannot_promote_without_reviewer_and_lifecycle() -> None:
    # Given: candidate evidence without terminal authorities
    candidate_ref = "a" * 64

    # When / Then: candidate evidence alone never changes strategy authority
    assert not promotion_is_authorized(candidate_ref, None, None)


def test_allocation_requires_two_independently_approved_champions() -> None:
    # Given: two persisted champion chains with independent strategy versions
    now = dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC)
    family_sources: tuple[tuple[AgentFamilyId, str], ...] = (
        ("day_trading", "a"),
        ("swing_trading", "b"),
    )
    champions = tuple(
        PersistedChampionAuthorityV1(
            strategy_version=f"strategy-{index}",
            family_id=family,
            reviewer=IndependentReviewerDecisionV1(
                candidate_ref=character * 64,
                reviewer_ref=str(index + 3) * 64,
                decision="accepted",
                decided_at=now,
            ),
            lifecycle=LifecycleAuthorityDecisionV1(
                candidate_ref=character * 64,
                lifecycle_ref=str(index + 5) * 64,
                state="shadow_champion",
                decided_at=now,
            ),
        )
        for index, (family, character) in enumerate(family_sources)
    )

    # When / Then: one remains locked and two unlock only the conditional role
    assert not allocation_manager_is_available(champions[:1])
    assert allocation_manager_is_available(champions)
