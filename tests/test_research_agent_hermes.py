from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from tests.test_research_agent_primary_actions import (
    CardResolver,
    _context,
    _context_evidence,
    _opportunity_evidence,
)
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.research_agent_cycle_models import (
    CycleId,
    ResearchAgentDecisionKind,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
    ResultId,
)
from trading_agent.research_agent_hermes import project_research_agent_results
from trading_agent.research_agent_primary_actions import (
    MarketContextResearchActionExecutor,
    OpportunityResearchActionExecutor,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _result(family: AgentFamilyId) -> ResearchAgentResultV1:
    cycle_id = CycleId(hashlib.sha256(f"{family}:cycle".encode()).hexdigest())
    return ResearchAgentResultV1(
        result_id=ResultId(hashlib.sha256(f"{cycle_id}:result".encode()).hexdigest()),
        cycle_id=cycle_id,
        agent_family_id=family,
        market_id="us_equities",
        status=ResearchAgentResultStatus.COMPLETED,
        question="Does the cited evidence support this bounded research conclusion?",
        summary=f"{family} completed one isolated evidence review.",
        reason=None,
        continuation=None,
        evidence_refs=(hashlib.sha256(f"{family}:evidence".encode()).hexdigest(),),
        artifact_refs=(hashlib.sha256(f"{family}:artifact".encode()).hexdigest(),),
        occurred_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
    )


def test_results_project_as_separate_agent_families_and_replay_once(tmp_path: Path) -> None:
    path = tmp_path / "delivery.sqlite3"
    store = HermesDeliveryStore(path)
    results = (_result("opportunity_manager"), _result("systematic_quant"))

    with store.writer() as writer:
        first = project_research_agent_results(results, writer)
        replay = project_research_agent_results(results, writer)
    events = HermesDeliveryReader(path).events()

    assert first.inserted == 2
    assert replay.inserted == 0
    assert {event.agent_family for event in events} == {
        "opportunity_manager",
        "systematic_quant",
    }
    assert all(event.kind.value == "research" for event in events)
    assert all(len(event.rendered_text) <= 4096 for event in events)


def test_primary_results_render_resolved_artifact_rows(tmp_path: Path) -> None:
    opportunity = _opportunity_evidence()
    context = _context_evidence()
    opportunity_result = OpportunityResearchActionExecutor(CardResolver(None)).execute(
        _context(opportunity, ResearchAgentDecisionKind.INVESTIGATE_CANDIDATE)
    )
    context_result = MarketContextResearchActionExecutor(lambda: ()).execute(
        _context(context, ResearchAgentDecisionKind.PUBLISH_CONTEXT)
    )
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    with store.writer() as writer:
        projected = project_research_agent_results(
            (opportunity_result, context_result),
            writer,
            evidence=(opportunity, context),
        )
    events = HermesDeliveryReader(store.path).events()
    by_family = {event.agent_family: event.rendered_text for event in events}

    assert projected.inserted == 2
    assert "ACME" in by_family["opportunity_manager"]
    assert "rank=1" in by_family["opportunity_manager"]
    assert "source=ranking_source" in by_family["opportunity_manager"]
    assert "investigation_reason=ranked_candidate" in by_family["opportunity_manager"]
    assert "risk_on" in by_family["market_context"]
    assert "advance_ratio=0.61" in by_family["market_context"]
    assert all("order authority: false" in text for text in by_family.values())
