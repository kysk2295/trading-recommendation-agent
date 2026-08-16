from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from trading_agent.dashboard_agent_runtime import (
    append_agent_runtime_readiness,
    project_agent_runtime,
)
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.research_agent_cycle_models import (
    EvidenceId,
    ResearchAgentDecisionKind,
    ResearchAgentEvidenceV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    research_agent_result_id,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore

NOW = dt.datetime(2026, 7, 27, 5, 30, tzinfo=dt.UTC)


def test_exact_six_agents_become_armed_from_three_channel_runtime_receipts(
    tmp_path: Path,
) -> None:
    outputs = tmp_path / "outputs"
    paths = append_agent_runtime_readiness(
        outputs,
        observed_at=NOW,
        code_sha256="a" * 40,
        state="armed",
    )

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    assert len(paths) == 18
    assert snapshot.workspaces.command_center.state == "populated"
    assert tuple(agent.runtime_state for agent in snapshot.workspaces.command_center.agents) == ("armed",) * 6
    assert all(
        agent.trace_id != snapshot.workspaces.command_center.trace_id
        for agent in snapshot.workspaces.command_center.agents
    )


def test_dashboard_readiness_comes_from_real_actor_cycles(tmp_path: Path) -> None:
    database = tmp_path / "cycles.sqlite3"
    store = ResearchAgentCycleStore(database)
    digest = hashlib.sha256(b"dashboard-runtime-evidence").hexdigest()
    evidence = ResearchAgentEvidenceV1(
        evidence_id=EvidenceId(digest),
        agent_family_id="opportunity_manager",
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key="dashboard.runtime.fixture",
        evidence_refs=(digest,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=digest,
        market_id="us_equities",
    )
    assert store.append_evidence(evidence)
    cycle = store.start_cycle(store.runnable_evidence("opportunity_manager", NOW)[0], NOW)
    result = ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle.cycle_id),
        cycle_id=cycle.cycle_id,
        agent_family_id=cycle.agent_family_id,
        market_id=cycle.market_id,
        status=ResearchAgentResultStatus.COMPLETED,
        question="Does the latest actor cycle prove the runtime state?",
        summary="The actor completed its bounded research cycle.",
        reason=None,
        continuation=None,
        evidence_refs=evidence.evidence_refs,
        artifact_refs=(hashlib.sha256(b"dashboard-runtime-artifact").hexdigest(),),
        occurred_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        decision_kind=ResearchAgentDecisionKind.INVESTIGATE_CANDIDATE,
    )
    store.finish_cycle(cycle, result)
    store.close()

    projection, agents = project_agent_runtime(
        tmp_path / "outputs",
        now=NOW,
        cycle_database=database,
    )

    assert projection.workspace.state == "blocked"
    assert next(item for item in agents if item.agent_id == "opportunity_manager").runtime_state == "idle"
    assert next(item for item in agents if item.agent_id == "day_trading").runtime_state == "unavailable"

    snapshot = collect_dashboard_snapshot_v2(
        tmp_path / "outputs",
        now=NOW,
        cycle_database=database,
    )
    rows = snapshot.workspaces.research.agent_cycles
    opportunity = next(row for row in rows if row.agent_family_id == "opportunity_manager")

    assert len(rows) == 6
    assert opportunity.input_source == "dashboard.runtime.fixture"
    assert opportunity.decision_kind == "investigate_candidate"
    assert opportunity.result_status == "completed"
    assert opportunity.artifact_count == 1
    assert opportunity.next_wake_kind == "new_evidence"
    assert opportunity.order_authority is False
