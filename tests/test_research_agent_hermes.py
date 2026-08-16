from __future__ import annotations

import datetime as dt
import hashlib
import json
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
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    ResultId,
)
from trading_agent.research_agent_cycle_store_codec import result_from_payload
from trading_agent.research_agent_hermes import project_research_agent_results
from trading_agent.research_agent_primary_actions import (
    MarketContextResearchActionExecutor,
    OpportunityResearchActionExecutor,
)
from trading_agent.research_agent_source_common import ResearchAgentEvidenceMaterial, canonical_payload_json

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


def test_known_result_ids_are_not_rerendered(tmp_path: Path) -> None:
    result = _result("market_context")
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    with store.writer() as writer:
        skipped = project_research_agent_results(
            (result,),
            writer,
            projected_result_ids=frozenset({result.result_id}),
        )

    assert skipped.examined == skipped.inserted == skipped.replayed == 0
    assert HermesDeliveryReader(store.path).events() == ()


def test_shipped_no_action_result_projects_after_store_validation(tmp_path: Path) -> None:
    current = _result("market_context").model_copy(
        update={
            "artifact_refs": (),
            "continuation": "Wait for the next completed market observation.",
            "reason": "no_material_change",
            "status": ResearchAgentResultStatus.NO_ACTION,
        }
    )
    payload = current.model_dump(mode="json")
    payload["artifact_refs"] = ["b" * 64]
    del payload["decision_kind"]
    shipped = result_from_payload(json.dumps(payload))
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    with store.writer() as writer:
        projected = project_research_agent_results((shipped,), writer)

    event = HermesDeliveryReader(store.path).events()[0]
    assert projected.inserted == 1
    assert event.status == "no_action"
    assert event.payload_sha256 == hashlib.sha256(
        shipped.model_dump_json(exclude_unset=True).encode()
    ).hexdigest()


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


def test_day_and_swing_results_render_existing_plan_and_outcome_rows(tmp_path: Path) -> None:
    day = _artifact_evidence("day_trading", "day.session.20260803", "day-plan")
    swing = _artifact_evidence("swing_trading", "swing.signal.abc12345", "swing-outcome")
    day_result = _result("day_trading").model_copy(
        update={
            "summary": (
                "recommendation=rec-1,symbol=ACME,timestamp=2026-08-03T14:34:00+00:00,"
                "entry=10.0,stop=9.5,targets=10.5,11.0,state=stopped,rationale=completed bar breakout"
            ),
            "artifact_refs": (day.payload_sha256,),
        }
    )
    swing_result = _result("swing_trading").model_copy(
        update={
            "summary": (
                "signal=swing-1,symbol=ACME,entry=15.04800,stop=13.86900,"
                "state=stopped,event=swing-1:stopped"
            ),
            "artifact_refs": (swing.payload_sha256,),
        }
    )
    store = HermesDeliveryStore(tmp_path / "delivery.sqlite3")

    with store.writer() as writer:
        projected = project_research_agent_results(
            (day_result, swing_result),
            writer,
            evidence=(day, swing),
        )
    rows = {event.agent_family: event.rendered_text for event in HermesDeliveryReader(store.path).events()}

    assert projected.inserted == 2
    assert "timestamp=2026-08-03T14:34:00+00:00" in rows["day_trading"]
    assert "targets=10.5,11.0" in rows["day_trading"]
    assert "rationale=completed bar breakout" in rows["day_trading"]
    assert "state=stopped" in rows["swing_trading"]
    assert "event=swing-1:stopped" in rows["swing_trading"]
    assert all("next wake=new_evidence" in row for row in rows.values())
    assert all("order authority: false" in row for row in rows.values())


def _artifact_evidence(family: AgentFamilyId, source_key: str, value: str):
    return ResearchAgentEvidenceMaterial(
        family=family,
        trigger=ResearchAgentTriggerKind.OPEN_WORK,
        source_key=source_key,
        observed_at=NOW,
        available_at=NOW,
        market_id="us_equities",
        canonical_payload=canonical_payload_json({"artifact": value}),
    ).evidence()
