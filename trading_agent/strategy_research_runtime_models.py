from __future__ import annotations

import datetime as dt
from typing import Literal, Protocol, Self

from pydantic import Field, model_validator

from trading_agent.strategy_research_experiment_models import ScienceCycleResult, ScienceExperiment
from trading_agent.strategy_research_holdout_reviewer import SealedHoldoutPayload
from trading_agent.strategy_research_models import ImmutableHypothesis
from trading_agent.strategy_research_observation_builders import SourceAuthorityReceipt
from trading_agent.strategy_research_policy import FeedbackWorkPurpose
from trading_agent.strategy_research_types import CanonicalModel, ResearchAgentId

type SlotState = Literal[
    "waiting_evidence",
    "waiting_due",
    "waiting_maturity",
    "due",
    "started",
    "recovery_pending",
    "completed",
    "waiting_feedback",
    "forward_shadow",
    "paper_candidate",
]


class InvalidStrategyResearchWorkSourceError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class StrategyResearchRuntimeBusyError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(
        self,
        reason: Literal[
            "experiment_ledger_writer_busy",
            "heavy_empirical_lease_busy",
            "science_kernel_rss_limit_reached",
        ] = "experiment_ledger_writer_busy",
    ) -> None:
        self.reason = reason
        super().__init__(self.reason)

    def __str__(self) -> str:
        return (
            f'{{"broker_mutation":0,"operation":"tick","reason":"{self.reason}",'
            '"status":"busy","trading_mutation":0}'
        )


class StrategyResearchWork(CanonicalModel):
    evidence_event_id: str = Field(min_length=1)
    available_at: dt.datetime
    maturity_at: dt.datetime
    draft: ImmutableHypothesis
    experiment: ScienceExperiment | None = None
    sealed_holdout: SealedHoldoutPayload | None = None
    feedback_purpose: FeedbackWorkPurpose = FeedbackWorkPurpose.INITIAL
    source_receipts: tuple[SourceAuthorityReceipt, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> Self:
        if (self.experiment is None) != (self.sealed_holdout is None):
            raise InvalidStrategyResearchWorkSourceError("work_outcome_payload_incomplete")
        return self


class StrategyResearchWorkSource(Protocol):
    def next_work(
        self,
        agent_id: ResearchAgentId,
        evidence_cursor: str | None,
    ) -> StrategyResearchWork | None: ...


class StrategyResearchCycleRunner(Protocol):
    def run(self, work: StrategyResearchWork) -> ScienceCycleResult: ...


class StrategyResearchAgentSlot(CanonicalModel):
    agent_id: ResearchAgentId
    state: SlotState
    evidence_cursor: str | None
    next_due_at: dt.datetime | None
    next_maturity_at: dt.datetime | None
    hypothesis_id: str | None
    attempt_id: str | None
    checkpoint_sha256: str | None
    retry_count: int = Field(ge=0)


class StrategyResearchRuntimeStatus(CanonicalModel):
    slots: tuple[StrategyResearchAgentSlot, ...] = Field(min_length=6, max_length=6)
    heavy_cycles_started: Literal[0, 1]
    heavy_agent_id: ResearchAgentId | None
    observed_at: dt.datetime
    broker_mutation: Literal[0] = 0
    trading_mutation: Literal[0] = 0

    def slot(self, agent_id: ResearchAgentId) -> StrategyResearchAgentSlot:
        return next(item for item in self.slots if item.agent_id is agent_id)


__all__ = (
    "InvalidStrategyResearchWorkSourceError",
    "SlotState",
    "StrategyResearchAgentSlot",
    "StrategyResearchCycleRunner",
    "StrategyResearchRuntimeBusyError",
    "StrategyResearchRuntimeStatus",
    "StrategyResearchWork",
    "StrategyResearchWorkSource",
)
