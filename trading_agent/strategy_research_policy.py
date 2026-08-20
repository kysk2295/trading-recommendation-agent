from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never, override

from trading_agent.strategy_research_methodologies import strategy_research_methodology
from trading_agent.strategy_research_models import ImmutableHypothesis
from trading_agent.strategy_research_results import TerminalResearchResult
from trading_agent.strategy_research_types import ResearchAgentId, SafeTerminalReason, TerminalOutcome

_SAFE_ARTIFACT_REF = re.compile(r"^artifact://safe/[0-9a-f]{64}$")

if TYPE_CHECKING:
    from trading_agent.strategy_research_runtime_models import StrategyResearchWork


@dataclass(frozen=True, slots=True)
class MethodologyPolicyError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


class FeedbackAction(StrEnum):
    FUTURE_ONLY_REPLICATION = "future_only_replication_or_shadow"
    NEW_LINEAGE_METHOD_CHANGE = "new_lineage_closed_method_change"
    WAIT_NAMED_EVIDENCE = "wait_for_named_evidence_or_maturity"


class FeedbackWorkPurpose(StrEnum):
    INITIAL = "initial"
    FUTURE_REPLICATION = "future_replication"
    NEW_LINEAGE_METHOD_CHANGE = "new_lineage_method_change"
    EVIDENCE_COMPLETION = "evidence_completion"


def require_validated_online_error_control(
    *,
    claimed: bool,
    evaluator_version: str | None,
    validation_artifact_ref: str | None,
) -> None:
    supplied = evaluator_version is not None or validation_artifact_ref is not None
    valid = (
        evaluator_version is not None
        and bool(evaluator_version.strip())
        and validation_artifact_ref is not None
        and _SAFE_ARTIFACT_REF.fullmatch(validation_artifact_ref) is not None
    )
    if claimed and not valid:
        raise MethodologyPolicyError("e_value_evaluator_validation_required")
    if not claimed and supplied:
        raise MethodologyPolicyError("unused_e_value_evaluator_forbidden")


@dataclass(frozen=True, slots=True)
class OwnerFeedbackDecision:
    result_id: str
    owner_agent_id: ResearchAgentId
    hypothesis_id: str
    action: FeedbackAction
    reason_codes: tuple[SafeTerminalReason, ...]
    next_test: str
    evaluated_at: dt.datetime


@dataclass(frozen=True, slots=True)
class FeedbackWorkAdmission:
    allowed: bool
    reason: str
    not_before: dt.datetime | None


class OwnerFeedbackRouter:
    __slots__ = ("_results",)

    def __init__(self, results: tuple[TerminalResearchResult, ...]) -> None:
        self._results = results

    def for_owner(self, owner_agent_id: ResearchAgentId) -> OwnerFeedbackDecision | None:
        owned = tuple(item for item in self._results if item.owner_agent_id is owner_agent_id)
        if not owned:
            return None
        result = max(owned, key=lambda item: (item.evaluated_at, item.result_id))
        policy = strategy_research_methodology(owner_agent_id)
        match result.outcome:
            case TerminalOutcome.SUPPORTED:
                action = FeedbackAction.FUTURE_ONLY_REPLICATION
                next_test = policy.next_test_policy
            case TerminalOutcome.REFUTED:
                action = FeedbackAction.NEW_LINEAGE_METHOD_CHANGE
                next_test = "close current method; preregister a changed method as a new lineage"
            case TerminalOutcome.INCONCLUSIVE:
                action = FeedbackAction.WAIT_NAMED_EVIDENCE
                reasons = ",".join(reason.value for reason in result.reason_codes)
                next_test = f"wait for maturity or evidence resolving:{reasons}"
            case unreachable:
                assert_never(unreachable)
        return OwnerFeedbackDecision(
            result_id=result.result_id,
            owner_agent_id=owner_agent_id,
            hypothesis_id=result.hypothesis_id,
            action=action,
            reason_codes=result.reason_codes,
            next_test=next_test,
            evaluated_at=result.evaluated_at,
        )


def admit_feedback_work(
    decision: OwnerFeedbackDecision,
    prior: ImmutableHypothesis,
    work: StrategyResearchWork,
) -> FeedbackWorkAdmission:
    parent_matches = work.draft.parent_hypothesis_id == decision.hypothesis_id
    match decision.action:
        case FeedbackAction.FUTURE_ONLY_REPLICATION:
            allowed = (
                work.feedback_purpose is FeedbackWorkPurpose.FUTURE_REPLICATION
                and parent_matches
                and work.available_at > decision.evaluated_at
                and work.maturity_at > work.available_at
            )
            return FeedbackWorkAdmission(
                allowed,
                "feedback_future_replication" if allowed else "feedback_waiting_future_replication",
                work.maturity_at if allowed else decision.evaluated_at,
            )
        case FeedbackAction.NEW_LINEAGE_METHOD_CHANGE:
            allowed = (
                work.feedback_purpose is FeedbackWorkPurpose.NEW_LINEAGE_METHOD_CHANGE
                and parent_matches
                and work.draft.hypothesis_id != prior.hypothesis_id
                and work.draft.search_family_id != prior.search_family_id
            )
            return FeedbackWorkAdmission(
                allowed,
                "feedback_new_lineage" if allowed else "feedback_refuted_lineage_closed",
                None,
            )
        case FeedbackAction.WAIT_NAMED_EVIDENCE:
            allowed = (
                work.feedback_purpose is FeedbackWorkPurpose.EVIDENCE_COMPLETION
                and parent_matches
                and work.available_at > decision.evaluated_at
            )
            return FeedbackWorkAdmission(
                allowed,
                "feedback_evidence_maturity" if allowed else "feedback_waiting_named_evidence",
                work.maturity_at if allowed else decision.evaluated_at,
            )
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "FeedbackAction",
    "FeedbackWorkAdmission",
    "FeedbackWorkPurpose",
    "MethodologyPolicyError",
    "OwnerFeedbackDecision",
    "OwnerFeedbackRouter",
    "admit_feedback_work",
    "require_validated_online_error_control",
)
