from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from trading_agent.research_agent_actions import InvalidResearchAgentActionError, ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ResearchAgentDecisionKind,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    research_agent_result_id,
)

_PROJECTION_KEYS = {
    "blocker_code",
    "edges",
    "items",
    "nodes",
    "observed_at",
    "projected_count",
    "state",
    "total_count",
    "truncated",
}
_ITEM_KEYS = {"item_id", "observed_at", "state", "value"}


@dataclass(frozen=True, slots=True)
class DerivativesResearchActionExecutor:
    prior_results: Callable[[], tuple[ResearchAgentResultV1, ...]]

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        if context.cycle.agent_family_id != "derivatives_research":
            raise InvalidResearchAgentActionError(reason="action_family_identity_mismatch")
        if context.decision.primary_decision is not ResearchAgentDecisionKind.PUBLISH_CONTEXT:
            raise InvalidResearchAgentActionError(reason="prose_only_result")
        evidence = _selected_evidence(context)
        if evidence.source_key.startswith("derivatives.blocked."):
            return _no_action(context, evidence.source_key, "Wait for a verified derivatives research capability.")
        projection = _projection(evidence.bounded_payload_json)
        if any(
            result.agent_family_id == "derivatives_research"
            and result.status is ResearchAgentResultStatus.COMPLETED
            and evidence.payload_sha256 in result.artifact_refs
            for result in self.prior_results()
        ):
            return _no_action(context, "derivatives_context_unchanged", "Wait for a changed derivatives projection.")
        items = projection["items"]
        if not isinstance(items, list):
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
        rows = tuple(_item_row(item) for item in items[:5])
        summary = "; ".join(rows) if rows else f"state={projection['state']},projected_count=0"
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(context.cycle.cycle_id),
            cycle_id=context.cycle.cycle_id,
            agent_family_id="derivatives_research",
            market_id=context.cycle.market_id,
            status=ResearchAgentResultStatus.COMPLETED,
            question=context.decision.question,
            summary=summary,
            evidence_refs=context.decision.evidence_refs,
            artifact_refs=(evidence.payload_sha256,),
            occurred_at=context.observed_at,
            next_wake_kind=context.decision.next_wake_kind,
            next_wake_at=context.decision.next_wake_at,
        )


def _selected_evidence(context: ResearchAgentActionContext):
    selected = set(context.decision.subject_refs)
    matches = tuple(
        item
        for item in context.evidence
        if selected.intersection((str(item.evidence_id), *item.subject_refs))
    )
    if len(matches) != 1 or matches[0].bounded_payload_json is None:
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
    return matches[0]


def _projection(payload_json: str | None) -> dict[str, object]:
    try:
        payload = json.loads(payload_json or "")
        if not isinstance(payload, dict) or set(payload) != {"interval_observed_at", "projection"}:
            raise ValueError
        projection = payload["projection"]
        if not isinstance(projection, dict) or set(projection) != _PROJECTION_KEYS:
            raise ValueError
        items = projection["items"]
        if (
            not isinstance(items, list)
            or len(items) > 24
            or any(not isinstance(item, dict) or set(item) != _ITEM_KEYS for item in items)
            or projection["projected_count"] != len(items)
        ):
            raise ValueError
        return projection
    except (TypeError, ValueError, json.JSONDecodeError):
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved") from None


def _item_row(item: object) -> str:
    if not isinstance(item, dict):
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
    item_id = item.get("item_id")
    state = item.get("state")
    value = item.get("value")
    if not isinstance(item_id, str) or not isinstance(state, str) or not (value is None or isinstance(value, str)):
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
    return f"item={item_id},state={state},value={(value or 'unavailable')[:80]}"


def _no_action(
    context: ResearchAgentActionContext,
    reason: str,
    continuation: str,
) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="derivatives_research",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=context.decision.question,
        summary="No new authoritative derivatives research artifact was published.",
        reason=reason,
        continuation=continuation,
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


__all__ = ("DerivativesResearchActionExecutor",)
