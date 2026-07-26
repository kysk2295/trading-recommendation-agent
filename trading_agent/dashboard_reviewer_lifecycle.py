from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading_agent.dashboard_agent_family import AgentFamilyId


class IndependentReviewerDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: str = Field(pattern=r"^(accepted|rejected|needs_evidence)$")
    decided_at: AwareDatetime


class LifecycleAuthorityDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    lifecycle_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: str = Field(pattern=r"^(shadow_champion|paper_champion|rejected|suspended)$")
    decided_at: AwareDatetime


class PersistedChampionAuthorityV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_version: str = Field(min_length=1, max_length=120)
    family_id: AgentFamilyId
    reviewer: IndependentReviewerDecisionV1
    lifecycle: LifecycleAuthorityDecisionV1


def promotion_is_authorized(
    candidate_ref: str,
    reviewer: IndependentReviewerDecisionV1 | None,
    lifecycle: LifecycleAuthorityDecisionV1 | None,
) -> bool:
    return (
        reviewer is not None
        and lifecycle is not None
        and reviewer.candidate_ref == candidate_ref
        and lifecycle.candidate_ref == candidate_ref
        and reviewer.decision == "accepted"
        and lifecycle.state in {"shadow_champion", "paper_champion"}
        and lifecycle.decided_at >= reviewer.decided_at
    )


def allocation_manager_is_available(
    champions: tuple[PersistedChampionAuthorityV1, ...],
) -> bool:
    independently_approved = {
        champion.strategy_version
        for champion in champions
        if promotion_is_authorized(
            champion.reviewer.candidate_ref,
            champion.reviewer,
            champion.lifecycle,
        )
    }
    return len(independently_approved) >= 2


__all__ = (
    "IndependentReviewerDecisionV1",
    "LifecycleAuthorityDecisionV1",
    "PersistedChampionAuthorityV1",
    "allocation_manager_is_available",
    "promotion_is_authorized",
)
