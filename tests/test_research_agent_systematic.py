from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

from trading_agent.research_agent_actions import ResearchAgentActionConfig, ResearchAgentActionExecutor
from trading_agent.research_agent_cycle_models import (
    ActionId,
    CycleId,
    DecisionId,
    EvidenceId,
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentResultStatus,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_systematic import (
    SystematicResearchActionConfig,
    SystematicResearchActionExecutor,
    systematic_cycle_command,
)

PROJECT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _cycle() -> ResearchAgentCycleV1:
    return ResearchAgentCycleV1(
        cycle_id=CycleId("a" * 64),
        evidence_id=EvidenceId("b" * 64),
        action_request_id=ActionId("c" * 64),
        agent_family_id="systematic_quant",
        market_id="none",
        evidence_sequence=1,
        cursor_before=0,
        state=ResearchAgentCycleState.STARTED,
        started_at=NOW,
        terminal_at=None,
        result_id=None,
    )


def _decision() -> ResearchAgentDecisionV1:
    return ResearchAgentDecisionV1(
        decision_id=DecisionId("d" * 64),
        cycle_id=CycleId("a" * 64),
        agent_family_id="systematic_quant",
        primary_decision=ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT,
        requested_action=ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT,
        question="Does the cited mechanism survive conservative costs?",
        summary="Run the existing bounded generated strategy experiment.",
        reason=None,
        continuation=None,
        open_work_ref=None,
        evidence_refs=("e" * 64,),
        decided_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        model_id="fixture-model-v1",
        prompt_sha256="f" * 64,
        response_sha256="1" * 64,
    )


def _config(tmp_path: Path) -> SystematicResearchActionConfig:
    uv = shutil.which("uv")
    assert uv is not None
    return SystematicResearchActionConfig(
        project_root=PROJECT,
        uv_executable=Path(uv),
        python_executable=Path(sys.executable),
        context=PROJECT / "examples" / "research" / "researcher-context-v1.json",
        response_fixture=PROJECT / "examples" / "research" / "researcher-response-fixture-v1.json",
        hermes_executable=None,
        model_id="hermes-researcher-v1",
        experiment_ledger=tmp_path / "experiment.sqlite3",
        receipt_root=tmp_path / "receipts",
        strategy_root=tmp_path / "strategies",
        manifest_root=tmp_path / "manifests",
        queue_root=tmp_path / "queue",
        input_csv=PROJECT / "examples" / "example_intraday.csv",
        data_foundation_manifest=PROJECT / "examples" / "data" / "us-vwap-reclaim-historical-fixture-v1.json",
        artifact_root=tmp_path / "experiments",
        review_root=tmp_path / "reviews",
        runs_root=tmp_path / "runs",
        max_runtime_seconds=30.0,
    )


def test_systematic_command_uses_unique_cycle_output_and_existing_guarded_cli(tmp_path: Path) -> None:
    command = systematic_cycle_command(_config(tmp_path), _cycle())

    assert Path(command[0]).name == "uv"
    assert "--offline" in command
    assert str(PROJECT / "run_autonomous_research_cycle.py") in command
    assert str(tmp_path / "runs" / _cycle().cycle_id / "output") in command


def test_systematic_action_runs_generated_strategy_and_parses_reviewer_result(tmp_path: Path) -> None:
    systematic = SystematicResearchActionExecutor(_config(tmp_path))
    executor = ResearchAgentActionExecutor(
        ResearchAgentActionConfig(systematic=systematic, verified_trade_signal_refs=frozenset())
    )

    result = executor.execute(_cycle(), _decision())

    assert result.status is ResearchAgentResultStatus.COMPLETED
    assert result.reason == "reviewer_hold"
    assert len(result.artifact_refs) == 4
    assert (tmp_path / "runs" / _cycle().cycle_id / "output" / "autonomous_research_cycle_ko.md").is_file()
    assert not (tmp_path / "paper_execution").exists()


def test_blocked_systematic_cycle_is_failed_with_fixed_retry_and_never_completed(tmp_path: Path) -> None:
    config = _config(tmp_path).model_copy(update={"python_executable": Path("/bin/false")})
    executor = SystematicResearchActionExecutor(config, clock=lambda: NOW)

    result = executor.execute(_cycle(), _decision())

    assert result.status is ResearchAgentResultStatus.FAILED
    assert result.reason == "cycle_or_evidence_invalid"
    assert result.next_wake_kind is ResearchAgentWakeKind.SCHEDULED
    assert result.next_wake_at == NOW + dt.timedelta(minutes=15)
