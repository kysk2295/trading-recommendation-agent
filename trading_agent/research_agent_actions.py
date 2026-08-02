from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, assert_never, final

from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
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
class ResearchAgentActionConfig:
    systematic: SystematicResearchAction
    verified_trade_signal_refs: frozenset[str]


@final
class ResearchAgentActionExecutor:
    __slots__ = ("_config",)

    def __init__(self, config: ResearchAgentActionConfig) -> None:
        self._config = config

    def execute(
        self,
        cycle: ResearchAgentCycleV1,
        decision: ResearchAgentDecisionV1,
    ) -> ResearchAgentResultV1:
        if cycle.cycle_id != decision.cycle_id or cycle.agent_family_id != decision.agent_family_id:
            raise InvalidResearchAgentActionError(reason="action_cycle_identity_mismatch")
        match decision.primary_decision:
            case ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT:
                if cycle.agent_family_id != "systematic_quant":
                    raise InvalidResearchAgentActionError(reason="heavy_experiment_systematic_only")
                return self._config.systematic.execute(cycle, decision)
            case ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION:
                reference = decision.open_work_ref
                if reference is None or reference not in self._config.verified_trade_signal_refs:
                    raise InvalidResearchAgentActionError(reason="verified_trade_signal_required")
                return result_from_decision(cycle, decision)
            case (
                ResearchAgentDecisionKind.INVESTIGATE_CANDIDATE
                | ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS
                | ResearchAgentDecisionKind.RUN_LIGHT_EXPERIMENT
                | ResearchAgentDecisionKind.PUBLISH_CONTEXT
                | ResearchAgentDecisionKind.REVIEW_OPEN_STATE
                | ResearchAgentDecisionKind.NO_ACTION
            ):
                return result_from_decision(cycle, decision)
            case unreachable:
                assert_never(unreachable)


def result_from_decision(
    cycle: ResearchAgentCycleV1,
    decision: ResearchAgentDecisionV1,
) -> ResearchAgentResultV1:
    match decision.primary_decision:
        case ResearchAgentDecisionKind.NO_ACTION:
            status = ResearchAgentResultStatus.NO_ACTION
        case (
            ResearchAgentDecisionKind.INVESTIGATE_CANDIDATE
            | ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS
            | ResearchAgentDecisionKind.RUN_LIGHT_EXPERIMENT
            | ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT
            | ResearchAgentDecisionKind.PUBLISH_CONTEXT
            | ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION
            | ResearchAgentDecisionKind.REVIEW_OPEN_STATE
        ):
            status = ResearchAgentResultStatus.COMPLETED
        case unreachable:
            assert_never(unreachable)
    artifacts = tuple(sorted((decision.prompt_sha256, decision.response_sha256)))
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle.cycle_id),
        cycle_id=cycle.cycle_id,
        agent_family_id=cycle.agent_family_id,
        market_id=cycle.market_id,
        status=status,
        question=decision.question,
        summary=decision.summary,
        reason=decision.reason,
        continuation=decision.continuation,
        evidence_refs=decision.evidence_refs,
        artifact_refs=artifacts,
        occurred_at=decision.decided_at,
        next_wake_kind=decision.next_wake_kind,
        next_wake_at=decision.next_wake_at,
    )


__all__ = (
    "InvalidResearchAgentActionError",
    "ResearchAgentActionConfig",
    "ResearchAgentActionExecutor",
    "SystematicResearchAction",
    "result_from_decision",
)
