from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from run_research_agent_runtime import main
from tests.research_agent_systematic_input_fixtures import (
    write_ready_systematic_input_activation,
)
from tests.test_research_agent_service_cli import _config
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_agent_actions import ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    CycleId,
    DecisionId,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
    research_agent_result_id,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_decision import ResearchAgentDecisionRequest
from trading_agent.research_agent_runtime import (
    ConfiguredResearchAgentEvidenceCollector,
    ResearchAgentRuntime,
    ResearchAgentRuntimeServices,
    ResearchAgentTickResult,
)
from trading_agent.research_agent_service_config import (
    canonical_research_agent_service_config_sha256,
    write_research_agent_service_config,
)
from trading_agent.research_agent_service_health import read_persisted_research_agent_service_health
from trading_agent.research_agent_service_runtime import run_service_cycle, run_service_tick, service_status

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


@dataclass(frozen=True, slots=True)
class HypothesisDecisionClient:
    calls: list[AgentFamilyId]

    def decide(self, request: ResearchAgentDecisionRequest) -> ResearchAgentDecisionV1:
        self.calls.append(request.agent_family_id)
        return ResearchAgentDecisionV1(
            decision_id=DecisionId(hashlib.sha256(f"{request.cycle_id}:seed".encode()).hexdigest()),
            cycle_id=request.cycle_id,
            agent_family_id=request.agent_family_id,
            primary_decision=ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS,
            requested_action=ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS,
            question="Does this capability evidence support a bounded research state?",
            summary="The capability evidence was reviewed without any trading authority.",
            reason=None,
            continuation=None,
            open_work_ref=None,
            subject_refs=request.evidence[0].subject_refs,
            evidence_refs=tuple(sorted({ref for item in request.evidence for ref in item.evidence_refs})),
            decided_at=request.requested_at,
            next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
            next_wake_at=None,
            model_id="fixture-seed-v1",
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
        )


@dataclass(frozen=True, slots=True)
class ArtifactActionClient:
    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(context.cycle.cycle_id),
            cycle_id=context.cycle.cycle_id,
            agent_family_id=context.cycle.agent_family_id,
            market_id=context.cycle.market_id,
            status=ResearchAgentResultStatus.COMPLETED,
            question=context.decision.question,
            summary="A deterministic service fixture artifact completed the bounded action.",
            reason=None,
            continuation=None,
            evidence_refs=context.decision.evidence_refs,
            artifact_refs=(context.evidence[0].payload_sha256,),
            occurred_at=context.observed_at,
            next_wake_kind=context.decision.next_wake_kind,
            next_wake_at=context.decision.next_wake_at,
        )


@dataclass(frozen=True, slots=True)
class NonSystematicTickRuntime:
    store: ResearchAgentCycleStore

    def tick(self, now: dt.datetime) -> ResearchAgentTickResult:
        del now
        return ResearchAgentTickResult(
            status="completed",
            agent_family_id="day_trading",
            cycle_id=CycleId("c" * 64),
            model_calls=1,
            recovered_cycles=0,
        )

    def close(self) -> None:
        self.store.close()


def test_idle_service_tick_reports_zero_model_and_broker_mutations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    with ExperimentLedgerStore(config.source_paths.experiment_ledger).writer():
        pass
    calls: list[AgentFamilyId] = []
    seed = ResearchAgentRuntime(
        ResearchAgentRuntimeServices(
            store=ResearchAgentCycleStore(config.cycle_database),
            collector=ConfiguredResearchAgentEvidenceCollector(config.source_paths),
            decisions=HypothesisDecisionClient(calls),
            actions=ArtifactActionClient(),
        )
    )
    seed_ticks = tuple(seed.tick(NOW) for _ in range(6))
    assert sum(tick.status == "no_action" for tick in seed_ticks) == 3
    assert sum(tick.status == "completed" for tick in seed_ticks) == 3
    qa_time = NOW + dt.timedelta(minutes=2, seconds=30)
    assert seed.tick(qa_time).status == "idle"
    completed_results = sum(result.status is ResearchAgentResultStatus.COMPLETED for result in seed.store.results())
    seed.close()
    config_path = (tmp_path / "private" / "runtime.json").absolute()
    assert write_research_agent_service_config(config_path, config)

    seeded_calls = len(calls)
    code = main(("tick", "--config", str(config_path)), clock=lambda: qa_time)

    assert code == 0
    assert len(calls) == seeded_calls
    report = json.loads(capsys.readouterr().out)
    assert report["role_agents"]["status"] == "idle"
    assert report["role_agents"]["model_calls"] == report["role_agents"]["broker_mutation"] == 0
    assert report["role_agents"]["projected_results"] == completed_results
    assert [slot["state"] for slot in report["strategy_research"]["slots"]] == ["waiting_evidence"] * 6


def test_cycle_cli_runs_one_canonical_family_pass_and_replay_is_idle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    config_path = (tmp_path / "private" / "runtime.json").absolute()
    assert write_research_agent_service_config(config_path, config)

    first_code = main(("cycle", "--config", str(config_path)), clock=lambda: NOW)
    first_output = capsys.readouterr().out
    assert first_code == 0
    first = json.loads(first_output)
    second_code = main(
        ("cycle", "--config", str(config_path)),
        clock=lambda: NOW + dt.timedelta(seconds=30),
    )
    second_output = capsys.readouterr().out
    assert second_code == 0
    second = json.loads(second_output)

    assert first["status"] == "complete"
    assert [item["agent_family_id"] for item in first["outcomes"]] == list(PRIMARY_AGENT_FAMILIES)
    assert (first["family_count"], first["model_calls"]) == (6, 0)
    assert first["broker_mutation"] == 0
    assert second["status"] == "idle"
    assert second["outcomes"] == []
    assert second["family_count"] == second["model_calls"] == second["broker_mutation"] == 0


def test_restarted_service_reports_persisted_family_cursors_and_next_wake(tmp_path: Path) -> None:
    # Given: one bounded canonical pass has been closed before a process restart.
    config = _config(tmp_path)
    first = run_service_cycle(config, NOW)
    assert first.family_count == 6
    first_cursors = {item.agent_family_id: item.cursor for item in first.family_runtime}

    # When: a new service runtime reads the same private cycle journal.
    resumed = service_status(config, NOW + dt.timedelta(seconds=30))

    # Then: all six persisted cursors and their next-wake contracts are observable.
    assert tuple(item.agent_family_id for item in resumed.family_runtime) == PRIMARY_AGENT_FAMILIES
    assert {item.agent_family_id: item.cursor for item in resumed.family_runtime} == first_cursors
    assert all(item.cursor > 0 for item in resumed.family_runtime)
    assert all(item.cycle_id is not None for item in resumed.family_runtime)
    assert all(item.next_wake_kind is not None for item in resumed.family_runtime)
    assert resumed.next_wake_kind is not None
    assert resumed.broker_mutation == 0


def test_blocked_systematic_input_keeps_service_armed_without_heavy_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    ResearchAgentCycleStore(config.cycle_database).close()
    child_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "trading_agent.research_agent_systematic_executor.launch_systematic_child",
        lambda command, _project_root: child_calls.append(tuple(command)),
    )

    report = service_status(config, NOW)

    assert report.status == "armed"
    assert report.systematic_input_status == "blocked"
    assert report.systematic_input_sha256 is None
    assert report.systematic_foundation_sha256 is None
    assert report.model_calls == report.broker_mutation == 0
    assert child_calls == []


def test_blocked_systematic_input_allows_non_systematic_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    runtime = NonSystematicTickRuntime(ResearchAgentCycleStore(config.cycle_database))
    child_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "trading_agent.research_agent_service_operations.build_service_runtime",
        lambda _config: runtime,
    )
    monkeypatch.setattr(
        "trading_agent.research_agent_systematic_executor.launch_systematic_child",
        lambda command, _project_root: child_calls.append(tuple(command)),
    )

    report = run_service_tick(config, NOW)

    assert report.status == "completed"
    assert report.agent_family_id == "day_trading"
    assert report.systematic_input_status == "blocked"
    assert report.model_calls == 1
    assert report.broker_mutation == 0
    assert child_calls == []
    persisted_report = json.loads(
        (config.output_root / "research-agent-runtime-status.json").read_text(encoding="utf-8")
    )
    assert persisted_report["schema_version"] == 2
    assert persisted_report["config_sha256"] == canonical_research_agent_service_config_sha256(config)
    health = read_persisted_research_agent_service_health(config.output_root)
    assert health.config_sha256 == canonical_research_agent_service_config_sha256(config)
    assert health.observed_at == NOW
    assert health.state == "ready"
    assert health.reason == "runtime_ready"


def test_ready_systematic_input_reports_only_bound_digests(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fixture = write_ready_systematic_input_activation(
        tmp_path / "production-input",
        config.systematic.input_activation,
    )
    ResearchAgentCycleStore(config.cycle_database).close()

    report = service_status(config, NOW)

    assert report.systematic_input_status == "ready"
    assert report.systematic_input_sha256 == fixture.activation.input_sha256
    assert report.systematic_foundation_sha256 == fixture.activation.foundation_sha256
    assert "path" not in report.model_dump_json()
