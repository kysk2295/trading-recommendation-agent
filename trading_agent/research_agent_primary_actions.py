from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.research_agent_actions import (
    InvalidResearchAgentActionError,
    ResearchAgentActionContext,
)
from trading_agent.research_agent_cycle_models import (
    ResearchAgentDecisionKind,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    research_agent_result_id,
)
from trading_agent.research_agent_source_common import opportunity_candidate_subject_ref
from trading_agent.signal_contract_models import OpportunityCandidate, OpportunitySnapshot


class OpportunityHypothesisResolver(Protocol):
    def matching_card_key(self, snapshot: OpportunitySnapshot) -> str | None: ...


class ArchivedMarketContextEvidenceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    checkpoint_count: int = Field(ge=0)
    database_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_count: int = Field(ge=0)
    latest_checkpoint_at: str | None
    latest_risk_at: str | None
    recommendation_count: int = Field(ge=0)
    risk_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    session: str = Field(pattern=r"^[0-9]{8}$")

    @model_validator(mode="after")
    def require_time_values(self) -> Self:
        if any(value is not None and not value.strip() for value in (self.latest_checkpoint_at, self.latest_risk_at)):
            raise ValueError("archived_context_time_invalid")
        return self


@dataclass(frozen=True, slots=True)
class ExperimentLedgerOpportunityHypothesisResolver:
    reader: ExperimentLedgerReader

    def matching_card_key(self, snapshot: OpportunitySnapshot) -> str | None:
        source_ids = {
            *(reference.canonical_id for reference in snapshot.evidence_refs),
            *(coverage.source_id for coverage in snapshot.source_coverage),
        }
        try:
            matching_source_keys = {
                str(stored.source_key)
                for stored in self.reader.research_sources()
                if stored.source.source_id in source_ids
            }
            matches = tuple(
                str(stored.card_key)
                for stored in self.reader.research_hypothesis_cards()
                if matching_source_keys.intersection(stored.card.research_source_keys)
            )
        except (InvalidExperimentLedgerSourceError, ValueError):
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved") from None
        if len(matches) > 1:
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
        return None if not matches else matches[0]


@dataclass(frozen=True, slots=True)
class OpportunityResearchActionExecutor:
    hypothesis_resolver: OpportunityHypothesisResolver

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        _require_action_identity(context, "opportunity_manager")
        decision = context.decision
        if decision.primary_decision not in {
            ResearchAgentDecisionKind.INVESTIGATE_CANDIDATE,
            ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS,
        }:
            raise InvalidResearchAgentActionError(reason="prose_only_result")
        evidence, snapshot = _opportunity_snapshot(context)
        candidates = _selected_candidates(evidence.source_key, snapshot, decision.subject_refs)
        if not candidates:
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
        artifacts = [evidence.payload_sha256]
        if decision.primary_decision is ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS:
            card_key = self.hypothesis_resolver.matching_card_key(snapshot)
            if card_key is None:
                raise InvalidResearchAgentActionError(reason="required_evidence_unavailable")
            artifacts.append(card_key)
        rows = "; ".join(_candidate_row(candidate, snapshot) for candidate in candidates)
        return _completed_result(context, rows, tuple(sorted(artifacts)))


@dataclass(frozen=True, slots=True)
class MarketContextResearchActionExecutor:
    prior_results: Callable[[], tuple[ResearchAgentResultV1, ...]]

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        _require_action_identity(context, "market_context")
        if context.decision.primary_decision is not ResearchAgentDecisionKind.PUBLISH_CONTEXT:
            raise InvalidResearchAgentActionError(reason="prose_only_result")
        evidence = _selected_evidence(context)
        if any(
            result.agent_family_id == "market_context"
            and result.status is ResearchAgentResultStatus.COMPLETED
            and evidence.payload_sha256 in result.artifact_refs
            for result in self.prior_results()
        ):
            return _context_unchanged_result(context)
        summary = _context_summary(evidence.bounded_payload_json)
        return _completed_result(context, summary, (evidence.payload_sha256,))


def _require_action_identity(context: ResearchAgentActionContext, family: str) -> None:
    if context.cycle.agent_family_id != family or context.decision.agent_family_id != family:
        raise InvalidResearchAgentActionError(reason="action_family_identity_mismatch")


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


def _opportunity_snapshot(context: ResearchAgentActionContext):
    evidence = _selected_evidence(context)
    try:
        payload = _authority_payload(evidence.bounded_payload_json)
        return evidence, OpportunitySnapshot.model_validate(payload)
    except (TypeError, ValidationError, ValueError):
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved") from None


def _authority_payload(payload_json: str | None) -> object:
    if payload_json is None:
        raise ValueError
    payload = json.loads(payload_json)
    if isinstance(payload, dict) and "source_payload" in payload:
        if payload.get("research_only") is not True or payload.get("trading_authority") is not False:
            raise ValueError
        return payload["source_payload"]
    return payload


def _selected_candidates(
    source_key: str,
    snapshot: OpportunitySnapshot,
    subject_refs: tuple[str, ...],
) -> tuple[OpportunityCandidate, ...]:
    selected = set(subject_refs)
    if source_key in selected:
        return snapshot.candidates
    return tuple(
        candidate
        for candidate in snapshot.candidates
        if opportunity_candidate_subject_ref(source_key, candidate.rank) in selected
    )


def _candidate_row(candidate: OpportunityCandidate, snapshot: OpportunitySnapshot) -> str:
    features = ",".join(f"{item.name}={item.value}" for item in candidate.features)
    sources = ",".join(item.source_id for item in snapshot.source_coverage)
    return (
        f"symbol={candidate.symbol},rank={candidate.rank},score={candidate.score},features={features},"
        f"source={sources},investigation_reason=ranked_candidate"
    )


def _context_summary(payload_json: str | None) -> str:
    try:
        payload = _authority_payload(payload_json)
        try:
            snapshot = MarketContextSnapshot.model_validate(payload)
        except ValidationError:
            archived = ArchivedMarketContextEvidenceV1.model_validate(payload)
            return (
                f"session={archived.session},event_count={archived.event_count},"
                f"recommendation_count={archived.recommendation_count},risk_sha256={archived.risk_sha256}"
            )
        regimes = ",".join(item.value for item in snapshot.regime_labels)
        features = ",".join(f"{item.name}={item.value}" for item in snapshot.breadth_and_volatility_features)
        sources = ",".join(item.source_id for item in snapshot.coverage)
        return f"regimes={regimes},features={features},source={sources}"
    except (TypeError, ValidationError, ValueError):
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved") from None


def _completed_result(
    context: ResearchAgentActionContext,
    summary: str,
    artifact_refs: tuple[str, ...],
) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id=context.cycle.agent_family_id,
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.COMPLETED,
        question=context.decision.question,
        summary=summary,
        reason=None,
        continuation=None,
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=artifact_refs,
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


def _context_unchanged_result(context: ResearchAgentActionContext) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id=context.cycle.agent_family_id,
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=context.decision.question,
        summary="The resolved Market Context artifact is unchanged.",
        reason="context_unchanged",
        continuation="Wait for a different Market Context artifact or the next scheduled wake.",
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


__all__ = (
    "ArchivedMarketContextEvidenceV1",
    "ExperimentLedgerOpportunityHypothesisResolver",
    "MarketContextResearchActionExecutor",
    "OpportunityHypothesisResolver",
    "OpportunityResearchActionExecutor",
)
