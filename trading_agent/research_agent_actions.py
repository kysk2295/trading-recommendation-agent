from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, final

from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    research_agent_result_id,
)


class InvalidResearchAgentActionError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class SystematicResearchAction(Protocol):
    def execute(
        self,
        cycle: ResearchAgentCycleV1,
        decision: ResearchAgentDecisionV1,
    ) -> ResearchAgentResultV1: ...


@dataclass(frozen=True, slots=True)
class ResearchAgentActionContext:
    cycle: ResearchAgentCycleV1
    evidence: tuple[ResearchAgentEvidenceV1, ...]
    open_work: tuple[ResearchAgentOpenWorkV1, ...]
    decision: ResearchAgentDecisionV1
    observed_at: dt.datetime


class ResearchAgentActionClient(Protocol):
    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1: ...


@dataclass(frozen=True, slots=True)
class ResearchAgentActionConfig:
    systematic: SystematicResearchAction


@final
class ResearchAgentActionExecutor:
    __slots__ = ("_config",)

    def __init__(self, config: ResearchAgentActionConfig) -> None:
        self._config = config

    def execute(
        self,
        context: ResearchAgentActionContext,
    ) -> ResearchAgentResultV1:
        cycle = context.cycle
        decision = context.decision
        if cycle.cycle_id != decision.cycle_id or cycle.agent_family_id != decision.agent_family_id:
            raise InvalidResearchAgentActionError(reason="action_cycle_identity_mismatch")
        if cycle.evidence_id not in {item.evidence_id for item in context.evidence}:
            raise InvalidResearchAgentActionError(reason="action_evidence_identity_mismatch")
        if any(item.agent_family_id != cycle.agent_family_id for item in (*context.evidence, *context.open_work)):
            raise InvalidResearchAgentActionError(reason="action_family_identity_mismatch")
        available_subjects = {
            reference
            for item in context.evidence
            for reference in (str(item.evidence_id), *item.subject_refs)
        } | {item.work_id for item in context.open_work}
        if not set(decision.subject_refs).issubset(available_subjects):
            raise InvalidResearchAgentActionError(reason="action_subject_identity_mismatch")
        match decision.primary_decision:
            case ResearchAgentDecisionKind.NO_ACTION:
                return _no_action_result(context)
            case ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT:
                if cycle.agent_family_id != "systematic_quant":
                    raise InvalidResearchAgentActionError(reason="heavy_experiment_systematic_only")
                return self._config.systematic.execute(cycle, decision)
            case (
                ResearchAgentDecisionKind.INVESTIGATE_CANDIDATE
                | ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS
                | ResearchAgentDecisionKind.RUN_LIGHT_EXPERIMENT
                | ResearchAgentDecisionKind.PUBLISH_CONTEXT
                | ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION
                | ResearchAgentDecisionKind.REVIEW_OPEN_STATE
            ):
                raise InvalidResearchAgentActionError(reason="prose_only_result")


def _no_action_result(context: ResearchAgentActionContext) -> ResearchAgentResultV1:
    cycle = context.cycle
    decision = context.decision
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle.cycle_id),
        cycle_id=cycle.cycle_id,
        agent_family_id=cycle.agent_family_id,
        market_id=cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=decision.question,
        summary=decision.summary,
        reason=decision.reason,
        continuation=decision.continuation,
        open_work_ref=decision.open_work_ref,
        evidence_refs=decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=decision.next_wake_kind,
        next_wake_at=decision.next_wake_at,
    )


__all__ = (
    "InvalidResearchAgentActionError",
    "ResearchAgentActionClient",
    "ResearchAgentActionConfig",
    "ResearchAgentActionContext",
    "ResearchAgentActionExecutor",
    "SystematicResearchAction",
)
