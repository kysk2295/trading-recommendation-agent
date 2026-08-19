from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.day_discovery_loop import DayDiscoveryError
from trading_agent.research_agent_actions import (
    InvalidResearchAgentActionError,
    ResearchAgentActionClient,
    ResearchAgentActionContext,
)
from trading_agent.research_agent_cycle_models import (
    ResearchAgentDecisionKind,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    research_agent_result_id,
)
from trading_agent.research_agent_source_common import require_private_source_file
from trading_agent.researcher_llm import ResearcherLlmError
from trading_agent.signal_contract_models import TradeSignalEnvelope
from trading_agent.trade_signal_outbox_reader import TradeSignalOutboxReaderError, read_trade_signal_publications


class DayEventArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: int = Field(ge=1)
    note: str
    occurred_at: dt.datetime
    price: float | None
    state: str


class DayRecommendationArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recommendation_id: str
    symbol: str
    strategy: str
    created_at: dt.datetime
    entry: float
    stop: float
    target_1r: float
    target_2r: float
    state: str
    rationale: str
    events: tuple[DayEventArtifact, ...]

    @model_validator(mode="after")
    def require_ordered_events(self) -> Self:
        identities = tuple(event.event_id for event in self.events)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("day_event_order_invalid")
        return self


class DayEvidenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    session: str = Field(pattern=r"^[0-9]{8}$")
    recommendations: tuple[DayRecommendationArtifact, ...]


@dataclass(frozen=True, slots=True)
class DayResearchActionExecutor:
    day_session_root: Path
    discovery: ResearchAgentActionClient | None = None

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        if context.cycle.agent_family_id != "day_trading":
            raise InvalidResearchAgentActionError(reason="action_family_identity_mismatch")
        decision = context.decision.primary_decision
        if decision is ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS:
            if self.discovery is None:
                raise InvalidResearchAgentActionError(reason="action_not_configured")
            try:
                return self.discovery.execute(context)
            except DayDiscoveryError as error:
                raise InvalidResearchAgentActionError(reason=error.reason) from None
            except ResearcherLlmError:
                raise InvalidResearchAgentActionError(reason="day_discovery_model_invalid") from None
            except ValidationError:
                raise InvalidResearchAgentActionError(reason="day_discovery_input_invalid") from None
        if decision not in {
            ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION,
            ResearchAgentDecisionKind.REVIEW_OPEN_STATE,
        }:
            raise InvalidResearchAgentActionError(reason="prose_only_result")
        evidence, payload = _selected_payload(context)
        recommendations = _selected_recommendations(context, payload)
        if not recommendations:
            return _no_setup_result(context)
        if len(recommendations) != 1:
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
        recommendation = recommendations[0]
        if decision is ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION:
            signal = self._resolve_signal(payload.session, recommendation)
            return _completed_result(
                context,
                _recommendation_summary(recommendation),
                (evidence.payload_sha256, signal.signal_id),
            )
        if not recommendation.events:
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
        event = recommendation.events[-1]
        event_ref = _event_artifact_ref(recommendation.recommendation_id, event.event_id)
        return _completed_result(
            context,
            _event_summary(recommendation, event),
            (evidence.payload_sha256, event_ref),
        )

    def _resolve_signal(
        self,
        session: str,
        recommendation: DayRecommendationArtifact,
    ) -> TradeSignalEnvelope:
        outbox = self.day_session_root / session / "trade-signals.v1.jsonl"
        try:
            require_private_source_file(outbox)
            signals = tuple(
                publication.signal
                for publication in read_trade_signal_publications(outbox)
                if publication.signal.signal_id == recommendation.recommendation_id
            )
        except (OSError, TradeSignalOutboxReaderError, ValueError):
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved") from None
        if len(signals) != 1 or not _signal_matches(recommendation, signals[0]):
            raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
        return signals[0]


def _selected_payload(context: ResearchAgentActionContext):
    selected = set(context.decision.subject_refs)
    matches = tuple(
        evidence
        for evidence in context.evidence
        if selected.intersection((str(evidence.evidence_id), *evidence.subject_refs))
    )
    if len(matches) != 1 or matches[0].bounded_payload_json is None:
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved")
    try:
        raw = json.loads(matches[0].bounded_payload_json)
        if isinstance(raw, dict) and "source_payload" in raw:
            if raw.get("research_only") is not True or raw.get("trading_authority") is not False:
                raise ValueError
            raw = raw["source_payload"]
        return matches[0], DayEvidenceArtifact.model_validate(raw)
    except (TypeError, ValidationError, ValueError):
        raise InvalidResearchAgentActionError(reason="authority_artifact_unresolved") from None


def _selected_recommendations(
    context: ResearchAgentActionContext,
    payload: DayEvidenceArtifact,
) -> tuple[DayRecommendationArtifact, ...]:
    selected = set(context.decision.subject_refs)
    specific = tuple(
        recommendation
        for recommendation in payload.recommendations
        if _recommendation_subject_ref(recommendation.recommendation_id) in selected
    )
    return specific or payload.recommendations


def _signal_matches(recommendation: DayRecommendationArtifact, signal: TradeSignalEnvelope) -> bool:
    targets = {target.label: target.price for target in signal.targets}
    return (
        signal.symbol == recommendation.symbol
        and signal.observed_at == recommendation.created_at
        and signal.entry_price == Decimal(str(recommendation.entry))
        and signal.stop_price == Decimal(str(recommendation.stop))
        and targets
        == {
            "1r": Decimal(str(recommendation.target_1r)),
            "2r": Decimal(str(recommendation.target_2r)),
        }
        and signal.rationale == recommendation.rationale
    )


def _recommendation_subject_ref(recommendation_id: str) -> str:
    digest = hashlib.sha256(recommendation_id.encode()).hexdigest()[:16]
    return f"day_recommendation.{digest}"


def _event_artifact_ref(recommendation_id: str, event_id: int) -> str:
    digest = hashlib.sha256(f"{recommendation_id}:{event_id}".encode()).hexdigest()[:16]
    return f"day_event.{digest}"


def _recommendation_summary(recommendation: DayRecommendationArtifact) -> str:
    return (
        f"recommendation={recommendation.recommendation_id},symbol={recommendation.symbol},"
        f"timestamp={recommendation.created_at.isoformat()},entry={recommendation.entry},"
        f"stop={recommendation.stop},targets={recommendation.target_1r},{recommendation.target_2r},"
        f"state={recommendation.state},rationale={recommendation.rationale}"
    )


def _event_summary(recommendation: DayRecommendationArtifact, event: DayEventArtifact) -> str:
    return (
        f"recommendation={recommendation.recommendation_id},symbol={recommendation.symbol},"
        f"state={event.state},event_id={event.event_id},occurred_at={event.occurred_at.isoformat()},"
        f"price={event.price},note={event.note}"
    )


def _completed_result(
    context: ResearchAgentActionContext,
    summary: str,
    artifacts: tuple[str, ...],
) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="day_trading",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.COMPLETED,
        question=context.decision.question,
        summary=summary,
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=tuple(sorted(artifacts)),
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


def _no_setup_result(context: ResearchAgentActionContext) -> ResearchAgentResultV1:
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id="day_trading",
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question=context.decision.question,
        summary="No existing Day recommendation artifact is available for this completed-bar evidence.",
        reason="no_setup",
        continuation="Wait for a new completed bar and an existing recommendation artifact.",
        evidence_refs=context.decision.evidence_refs,
        artifact_refs=(),
        occurred_at=context.observed_at,
        next_wake_kind=context.decision.next_wake_kind,
        next_wake_at=context.decision.next_wake_at,
    )


__all__ = ("DayResearchActionExecutor",)
