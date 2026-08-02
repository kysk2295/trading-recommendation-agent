from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.research_agent_cycle_models import (
    CycleId,
    DecisionId,
    EvidenceId,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    research_agent_action_id,
    research_agent_cycle_id,
    research_agent_result_id,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _evidence() -> ResearchAgentEvidenceV1:
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId("e" * 64),
        agent_family_id="opportunity_manager",
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key="source.kr.cycle.001",
        evidence_refs=("a" * 64,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256="b" * 64,
        market_id="kr_equities",
    )


def test_cycle_identity_binds_actor_trigger_and_cursor() -> None:
    evidence = _evidence()

    first = research_agent_cycle_id(evidence, cursor_before=0)

    assert first == research_agent_cycle_id(evidence, cursor_before=0)
    assert first != research_agent_cycle_id(evidence, cursor_before=1)
    assert research_agent_action_id(first) == research_agent_action_id(first)
    assert research_agent_result_id(first) == research_agent_result_id(first)


def test_result_cannot_claim_order_or_lifecycle_authority() -> None:
    cycle_id = CycleId("d" * 64)

    result = ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle_id),
        cycle_id=cycle_id,
        agent_family_id="systematic_quant",
        market_id="us_equities",
        status=ResearchAgentResultStatus.COMPLETED,
        question="Does the cited mechanism survive conservative costs?",
        summary="The deterministic Reviewer returned HOLD.",
        reason=None,
        continuation=None,
        evidence_refs=("a" * 64,),
        artifact_refs=("b" * 64,),
        occurred_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
    )

    assert result.order_authority is False
    assert result.lifecycle_authority is False
    assert result.allocation_authority is False


def test_evidence_rejects_duplicate_or_unsorted_references() -> None:
    with pytest.raises(ValidationError, match="sorted_unique_references_required"):
        ResearchAgentEvidenceV1(
            evidence_id=EvidenceId("e" * 64),
            agent_family_id="market_context",
            trigger_kind=ResearchAgentTriggerKind.MARKET_EVENT,
            source_key="market.regime.001",
            evidence_refs=("b" * 64, "a" * 64, "a" * 64),
            observed_at=NOW,
            available_at=NOW,
            payload_sha256="c" * 64,
            market_id="cross_market",
        )


def test_no_action_decision_requires_reason_and_continuation() -> None:
    with pytest.raises(ValidationError, match="no_action_continuation_required"):
        ResearchAgentDecisionV1(
            decision_id=DecisionId("f" * 64),
            cycle_id=CycleId("d" * 64),
            agent_family_id="day_trading",
            primary_decision=ResearchAgentDecisionKind.NO_ACTION,
            question="Is a current-session setup eligible?",
            summary="No completed eligible setup exists.",
            reason=None,
            continuation=None,
            evidence_refs=("a" * 64,),
            decided_at=NOW,
            next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
            next_wake_at=None,
        )


def test_scheduled_wake_requires_a_future_time() -> None:
    with pytest.raises(ValidationError, match="scheduled_wake_time_required"):
        ResearchAgentResultV1(
            result_id=research_agent_result_id(CycleId("d" * 64)),
            cycle_id=CycleId("d" * 64),
            agent_family_id="derivatives_research",
            market_id="us_equities",
            status=ResearchAgentResultStatus.BLOCKED,
            question="Is an entitled IV surface available?",
            summary="The required entitlement is unavailable.",
            reason="blocked_by_data",
            continuation="Retry after the next capability snapshot.",
            evidence_refs=("a" * 64,),
            artifact_refs=(),
            occurred_at=NOW,
            next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
            next_wake_at=None,
        )
