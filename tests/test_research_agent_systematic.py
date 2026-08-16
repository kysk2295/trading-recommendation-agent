from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Literal, assert_never
from unittest.mock import Mock

import pytest

from tests.research_agent_systematic_input_fixtures import (
    ReadySystematicInputFixture,
    write_ready_systematic_input_activation,
)
from trading_agent.research_agent_actions import (
    ResearchAgentActionConfig,
    ResearchAgentActionContext,
    ResearchAgentActionExecutor,
)
from trading_agent.research_agent_cycle_models import (
    ActionId,
    CycleId,
    DecisionId,
    EvidenceId,
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultStatus,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_systematic import (
    SystematicResearchActionConfig,
    SystematicResearchActionExecutor,
    systematic_cycle_command,
)
from trading_agent.research_agent_systematic_input_evidence import (
    VerifiedSystematicInputEvidence,
    verify_systematic_input_evidence_graph,
)
from trading_agent.research_agent_systematic_input_models import (
    BlockedSystematicInputActivation,
)
from trading_agent.research_agent_systematic_input_store import (
    write_systematic_input_activation,
)

PROJECT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)
type UnavailableActivationState = Literal[
    "missing",
    "malformed",
    "blocked",
    "tampered",
    "disconnected",
]


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
        subject_refs=("systematic_quant.subject.001",),
        evidence_refs=("e" * 64,),
        decided_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        model_id="fixture-model-v1",
        prompt_sha256="f" * 64,
        response_sha256="1" * 64,
    )


def _evidence() -> ResearchAgentEvidenceV1:
    payload = '{"status":"ready"}'
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId("b" * 64),
        agent_family_id="systematic_quant",
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key="systematic_quant.subject.001",
        evidence_refs=(digest,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=digest,
        market_id="none",
        bounded_payload_json=payload,
        subject_refs=("systematic_quant.subject.001",),
    )


def _context() -> ResearchAgentActionContext:
    return ResearchAgentActionContext(
        cycle=_cycle(),
        evidence=(_evidence(),),
        open_work=(),
        decision=_decision(),
        observed_at=NOW,
    )


def _config(
    tmp_path: Path,
    ready_input: ReadySystematicInputFixture | None = None,
) -> SystematicResearchActionConfig:
    uv = shutil.which("uv")
    assert uv is not None
    activated = ready_input or write_ready_systematic_input_activation(
        tmp_path / "production-input",
        tmp_path / "systematic-input.json",
    )
    return SystematicResearchActionConfig(
        project_root=PROJECT,
        uv_executable=Path(uv),
        python_executable=Path(sys.executable),
        context=PROJECT / "examples" / "research" / "researcher-context-v1.json",
        response_fixture=PROJECT / "examples" / "research" / "researcher-response-fixture-v1.json",
        hermes_executable=None,
        model_id="hermes-researcher-v1",
        provider_id="fixture-provider",
        experiment_ledger=tmp_path / "experiment.sqlite3",
        receipt_root=tmp_path / "receipts",
        strategy_root=tmp_path / "strategies",
        manifest_root=tmp_path / "manifests",
        queue_root=tmp_path / "queue",
        input_activation=activated.activation_path,
        artifact_root=tmp_path / "experiments",
        review_root=tmp_path / "reviews",
        runs_root=tmp_path / "runs",
        max_runtime_seconds=30.0,
    )


def test_systematic_command_uses_verified_input_and_existing_guarded_cli(tmp_path: Path) -> None:
    ready = write_ready_systematic_input_activation(
        tmp_path / "production-input",
        tmp_path / "systematic-input.json",
    )
    command = systematic_cycle_command(_config(tmp_path, ready), _cycle(), ready.activation)

    assert Path(command[0]).name == "uv"
    assert "--offline" in command
    assert str(PROJECT / "run_autonomous_research_cycle.py") in command
    assert str(tmp_path / "runs" / _cycle().cycle_id / "output") in command
    assert command[command.index("--input-csv") + 1] == str(ready.graph.input_csv_path)
    assert command[command.index("--data-foundation-manifest") + 1] == str(ready.graph.foundation_path)


def test_systematic_hermes_command_binds_explicit_provider(tmp_path: Path) -> None:
    config = _config(tmp_path).model_copy(
        update={
            "response_fixture": None,
            "hermes_executable": Path("/bin/echo"),
            "provider_id": "openai-codex",
        }
    )

    ready = write_ready_systematic_input_activation(
        tmp_path / "second-production-input",
        tmp_path / "second-systematic-input.json",
    )
    command = systematic_cycle_command(config, _cycle(), ready.activation)

    position = command.index("--provider-id")
    assert command[position + 1] == "openai-codex"


def test_systematic_action_runs_generated_strategy_and_parses_reviewer_result(tmp_path: Path) -> None:
    systematic = SystematicResearchActionExecutor(_config(tmp_path))
    executor = ResearchAgentActionExecutor(ResearchAgentActionConfig(systematic=systematic))

    result = executor.execute(_context())

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


@pytest.mark.parametrize(
    "activation_state",
    ["missing", "malformed", "blocked", "tampered", "disconnected"],
)
def test_unavailable_production_input_fails_without_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    activation_state: UnavailableActivationState,
) -> None:
    ready = write_ready_systematic_input_activation(
        tmp_path / "production-input",
        tmp_path / "systematic-input.json",
    )
    match activation_state:
        case "missing":
            ready.activation_path.unlink()
        case "malformed":
            ready.activation_path.write_text(json.dumps({"status": "ready"}), encoding="utf-8")
            ready.activation_path.chmod(0o600)
        case "blocked":
            write_systematic_input_activation(
                ready.activation_path,
                BlockedSystematicInputActivation(reason_code="no_connected_graph", attempted_at=NOW),
            )
        case "tampered":
            ready.graph.input_csv_path.write_text("tampered\n", encoding="utf-8")
        case "disconnected":
            second = write_ready_systematic_input_activation(
                tmp_path / "second-production-input",
                tmp_path / "second-systematic-input.json",
            )
            write_systematic_input_activation(
                ready.activation_path,
                ready.activation.model_copy(
                    update={
                        "catalog_receipt_path": second.activation.catalog_receipt_path,
                        "catalog_receipt_sha256": second.activation.catalog_receipt_sha256,
                    }
                ),
            )
        case unreachable:
            assert_never(unreachable)
    run = Mock(side_effect=AssertionError("subprocess must not run"))
    monkeypatch.setattr("trading_agent.research_agent_systematic.subprocess.run", run)
    executor = SystematicResearchActionExecutor(_config(tmp_path, ready), clock=lambda: NOW)

    result = executor.execute(_cycle(), _decision())

    assert result.status is ResearchAgentResultStatus.FAILED
    assert result.reason == "production_input_unavailable"
    assert result.next_wake_kind is ResearchAgentWakeKind.SCHEDULED
    assert result.next_wake_at == NOW + dt.timedelta(minutes=15)
    run.assert_not_called()


def test_pointer_swap_during_graph_verification_fails_without_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = write_ready_systematic_input_activation(
        tmp_path / "production-input",
        tmp_path / "systematic-input.json",
    )

    def swap_pointer(root: Path) -> VerifiedSystematicInputEvidence:
        facts = verify_systematic_input_evidence_graph(root)
        write_systematic_input_activation(
            ready.activation_path,
            BlockedSystematicInputActivation(reason_code="activation_replaced", attempted_at=NOW),
        )
        return facts

    run = Mock(side_effect=AssertionError("subprocess must not run"))
    monkeypatch.setattr(
        "trading_agent.research_agent_systematic_input_runtime.verify_systematic_input_evidence_graph",
        swap_pointer,
    )
    monkeypatch.setattr("trading_agent.research_agent_systematic.subprocess.run", run)
    executor = SystematicResearchActionExecutor(_config(tmp_path, ready), clock=lambda: NOW)

    result = executor.execute(_cycle(), _decision())

    assert result.status is ResearchAgentResultStatus.FAILED
    assert result.reason == "production_input_unavailable"
    assert result.next_wake_at == NOW + dt.timedelta(minutes=15)
    run.assert_not_called()
