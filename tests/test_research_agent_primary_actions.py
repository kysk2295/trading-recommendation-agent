from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

import pytest

from trading_agent.market_context_models import MarketContextSnapshot, MarketRegimeLabel
from trading_agent.research_agent_actions import InvalidResearchAgentActionError, ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ActionId,
    CycleId,
    DecisionId,
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultStatus,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_primary_actions import (
    MarketContextResearchActionExecutor,
    OpportunityResearchActionExecutor,
)
from trading_agent.research_agent_source_common import (
    ResearchAgentEvidenceMaterial,
    canonical_model_json,
    canonical_payload_json,
    opportunity_candidate_subject_ref,
)
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)

NOW = dt.datetime(2026, 8, 3, 14, 35, tzinfo=dt.UTC)
CARD_KEY = "c" * 64


class CardResolver:
    def __init__(self, key: str | None) -> None:
        self.key = key

    def matching_card_key(self, snapshot: OpportunitySnapshot) -> str | None:
        del snapshot
        return self.key


def _opportunity() -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id="us-opportunity-20260803t143400-abcd1234",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="ranking-momentum-v1",
        observed_at=NOW,
        valid_until=NOW + dt.timedelta(minutes=2),
        candidates=(
            OpportunityCandidate(
                symbol="ACME",
                rank=1,
                score=Decimal("0.12"),
                features=(
                    FeatureValue(name="change_pct", value="0.12"),
                    FeatureValue(name="spread_bps", value="12.5"),
                ),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="ranking", record_id="nas:1:acme", observed_at=NOW),),
        source_coverage=(SourceCoverage(source_id="ranking_source", observed_at=NOW, record_count=1, complete=True),),
    )


def _opportunity_evidence() -> ResearchAgentEvidenceV1:
    snapshot = _opportunity()
    source_key = f"opportunity.{snapshot.opportunity_id}"
    return ResearchAgentEvidenceMaterial(
        family="opportunity_manager",
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=source_key,
        observed_at=NOW,
        available_at=NOW,
        market_id="us_equities",
        canonical_payload=canonical_model_json(snapshot),
        subject_refs=(source_key, opportunity_candidate_subject_ref(source_key, 1)),
    ).evidence()


def _context_evidence() -> ResearchAgentEvidenceV1:
    snapshot = MarketContextSnapshot(
        context_id="us-context-20260803t143500",
        market_id=MarketId.US_EQUITIES,
        observed_at=NOW,
        valid_until=NOW + dt.timedelta(minutes=30),
        regime_labels=(MarketRegimeLabel.RISK_ON,),
        breadth_and_volatility_features=(FeatureValue(name="advance_ratio", value="0.61"),),
        macro_and_flow_refs=("macro.ref.001",),
        coverage=(SourceCoverage(source_id="breadth", observed_at=NOW, record_count=2, complete=True),),
        producer_version="breadth-v1",
    )
    return ResearchAgentEvidenceMaterial(
        family="market_context",
        trigger=ResearchAgentTriggerKind.MARKET_EVENT,
        source_key=f"market_context.{snapshot.context_id}",
        observed_at=NOW,
        available_at=NOW,
        market_id="us_equities",
        canonical_payload=canonical_model_json(snapshot),
    ).evidence()


def _cycle(evidence: ResearchAgentEvidenceV1) -> ResearchAgentCycleV1:
    return ResearchAgentCycleV1(
        cycle_id=CycleId(hashlib.sha256(f"{evidence.evidence_id}:cycle".encode()).hexdigest()),
        evidence_id=evidence.evidence_id,
        action_request_id=ActionId("a" * 64),
        agent_family_id=evidence.agent_family_id,
        market_id=evidence.market_id,
        evidence_sequence=1,
        cursor_before=0,
        state=ResearchAgentCycleState.STARTED,
        started_at=NOW,
    )


def _context(evidence: ResearchAgentEvidenceV1, kind: ResearchAgentDecisionKind) -> ResearchAgentActionContext:
    cycle = _cycle(evidence)
    selected = () if kind is ResearchAgentDecisionKind.NO_ACTION else (evidence.subject_refs[-1],)
    decision = ResearchAgentDecisionV1(
        decision_id=DecisionId("d" * 64),
        cycle_id=cycle.cycle_id,
        agent_family_id=evidence.agent_family_id,
        primary_decision=kind,
        requested_action=kind,
        question="Which resolved artifact supports this bounded family action?",
        summary="Model prose is provenance only and is not an authority artifact.",
        subject_refs=selected,
        evidence_refs=evidence.evidence_refs,
        decided_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        model_id="fixture-model-v1",
        prompt_sha256="1" * 64,
        response_sha256="2" * 64,
    )
    return ResearchAgentActionContext(cycle, (evidence,), (), decision, NOW)


def test_opportunity_candidate_action_returns_resolved_snapshot_artifact() -> None:
    evidence = _opportunity_evidence()
    executor = OpportunityResearchActionExecutor(CardResolver(None))

    result = executor.execute(_context(evidence, ResearchAgentDecisionKind.INVESTIGATE_CANDIDATE))

    assert result.status is ResearchAgentResultStatus.COMPLETED
    assert result.artifact_refs == (evidence.payload_sha256,)
    assert "ACME" in result.summary
    assert "rank=1" in result.summary
    assert "spread_bps=12.5" in result.summary
    assert "source=ranking_source" in result.summary
    assert "investigation_reason=ranked_candidate" in result.summary


def test_opportunity_hypothesis_uses_only_an_existing_matching_card() -> None:
    evidence = _opportunity_evidence()
    context = _context(evidence, ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS)

    result = OpportunityResearchActionExecutor(CardResolver(CARD_KEY)).execute(context)

    assert result.artifact_refs == tuple(sorted((CARD_KEY, evidence.payload_sha256)))
    with pytest.raises(InvalidResearchAgentActionError, match="required_evidence_unavailable"):
        OpportunityResearchActionExecutor(CardResolver(None)).execute(context)


def test_context_action_publishes_typed_artifact_and_deduplicates_prior_result() -> None:
    evidence = _context_evidence()
    context = _context(evidence, ResearchAgentDecisionKind.PUBLISH_CONTEXT)
    first = MarketContextResearchActionExecutor(lambda: ()).execute(context)

    repeated = MarketContextResearchActionExecutor(lambda: (first,)).execute(context)

    assert first.status is ResearchAgentResultStatus.COMPLETED
    assert first.artifact_refs == (evidence.payload_sha256,)
    assert "risk_on" in first.summary
    assert "advance_ratio=0.61" in first.summary
    assert repeated.status is ResearchAgentResultStatus.NO_ACTION
    assert repeated.reason == "context_unchanged"
    assert repeated.artifact_refs == ()


def test_context_action_accepts_exact_archived_day_context() -> None:
    payload = canonical_payload_json(
        {
            "research_only": True,
            "source_payload": {
                "checkpoint_count": 2,
                "database_sha256": "a" * 64,
                "event_count": 4,
                "latest_checkpoint_at": NOW.isoformat(),
                "latest_risk_at": NOW.isoformat(),
                "recommendation_count": 1,
                "risk_sha256": "b" * 64,
                "session": "20260803",
            },
            "trading_authority": False,
        }
    )
    evidence = ResearchAgentEvidenceMaterial(
        family="market_context",
        trigger=ResearchAgentTriggerKind.MARKET_EVENT,
        source_key="market_context.research_archive.day.20260803",
        observed_at=NOW,
        available_at=NOW,
        market_id="cross_market",
        canonical_payload=payload,
    ).evidence()

    result = MarketContextResearchActionExecutor(lambda: ()).execute(
        _context(evidence, ResearchAgentDecisionKind.PUBLISH_CONTEXT)
    )

    assert result.artifact_refs == (evidence.payload_sha256,)
    assert "session=20260803" in result.summary
    assert "event_count=4" in result.summary
