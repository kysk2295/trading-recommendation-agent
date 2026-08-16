from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from tests.research_agent_primary_fixtures import source_paths
from tests.test_dashboard_projection_derivatives import seed_indicative_options
from trading_agent.research_agent_actions import ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ActionId,
    CycleId,
    DecisionId,
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentResultStatus,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_derivatives_actions import DerivativesResearchActionExecutor
from trading_agent.research_agent_source_adapter_derivatives import DerivativesSourceAdapter

NOW = dt.datetime(2026, 8, 3, 14, 35, tzinfo=dt.UTC)


def test_publishes_existing_indicative_derivatives_projection_as_research_only_artifact(
    tmp_path: Path,
) -> None:
    paths = source_paths(tmp_path)
    seed_indicative_options(paths.outputs_root, NOW)
    evidence = DerivativesSourceAdapter().collect(paths, NOW + dt.timedelta(minutes=1))[0]
    action = DerivativesResearchActionExecutor(lambda: ())

    result = action.execute(_context(evidence))

    assert result.status is ResearchAgentResultStatus.COMPLETED
    assert result.artifact_refs == (evidence.payload_sha256,)
    assert "option" in result.summary.lower()
    assert result.order_authority is False
    assert result.lifecycle_authority is False
    assert result.allocation_authority is False


def test_missing_derivatives_capability_is_typed_no_action(tmp_path: Path) -> None:
    evidence = DerivativesSourceAdapter().collect(source_paths(tmp_path), NOW)[0]
    action = DerivativesResearchActionExecutor(lambda: ())

    result = action.execute(_context(evidence))

    assert result.status is ResearchAgentResultStatus.NO_ACTION
    assert result.reason == "derivatives.blocked.options_entitlement_missing"
    assert result.artifact_refs == ()


def _context(evidence) -> ResearchAgentActionContext:
    cycle_id = CycleId(hashlib.sha256(str(evidence.evidence_id).encode()).hexdigest())
    cycle = ResearchAgentCycleV1(
        cycle_id=cycle_id,
        evidence_id=evidence.evidence_id,
        action_request_id=ActionId("a" * 64),
        agent_family_id="derivatives_research",
        market_id=evidence.market_id,
        evidence_sequence=1,
        cursor_before=0,
        state=ResearchAgentCycleState.STARTED,
        started_at=NOW,
    )
    decision = ResearchAgentDecisionV1(
        decision_id=DecisionId("d" * 64),
        cycle_id=cycle_id,
        agent_family_id="derivatives_research",
        primary_decision=ResearchAgentDecisionKind.PUBLISH_CONTEXT,
        requested_action=ResearchAgentDecisionKind.PUBLISH_CONTEXT,
        question="What does the bounded derivatives projection show now?",
        summary="Publish the existing projection as research-only context.",
        subject_refs=evidence.subject_refs,
        evidence_refs=evidence.evidence_refs,
        decided_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        model_id="fixture-model-v1",
        prompt_sha256="b" * 64,
        response_sha256="c" * 64,
    )
    return ResearchAgentActionContext(cycle, (evidence,), (), decision, NOW)
