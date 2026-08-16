from __future__ import annotations

import datetime as dt
import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.research_agent_systematic_input_fixtures import (
    write_ready_systematic_input_activation,
)
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_actions import (
    InvalidResearchAgentActionError,
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
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    research_agent_result_id,
)
from trading_agent.research_agent_systematic import (
    SystematicResearchActionConfig,
    SystematicResearchActionExecutor,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)
PROJECT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class RecordingPrimaryActionClient:
    contexts: list[ResearchAgentActionContext]

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        self.contexts.append(context)
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(context.cycle.cycle_id),
            cycle_id=context.cycle.cycle_id,
            agent_family_id=context.cycle.agent_family_id,
            market_id=context.cycle.market_id,
            status=ResearchAgentResultStatus.COMPLETED,
            question=context.decision.question,
            summary="The configured primary client resolved an authority artifact.",
            evidence_refs=context.decision.evidence_refs,
            artifact_refs=(context.evidence[0].payload_sha256,),
            occurred_at=context.observed_at,
            next_wake_kind=context.decision.next_wake_kind,
            next_wake_at=context.decision.next_wake_at,
        )


def _cycle(family: AgentFamilyId) -> ResearchAgentCycleV1:
    return ResearchAgentCycleV1(
        cycle_id=CycleId("a" * 64),
        evidence_id=EvidenceId("b" * 64),
        action_request_id=ActionId("c" * 64),
        agent_family_id=family,
        market_id="us_equities",
        evidence_sequence=1,
        cursor_before=0,
        state=ResearchAgentCycleState.STARTED,
        started_at=NOW,
        terminal_at=None,
        result_id=None,
    )


def _decision(family: AgentFamilyId, kind: ResearchAgentDecisionKind) -> ResearchAgentDecisionV1:
    no_action = kind is ResearchAgentDecisionKind.NO_ACTION
    subject_refs = () if no_action else (f"{family}.subject.001",)
    return ResearchAgentDecisionV1(
        decision_id=DecisionId("d" * 64),
        cycle_id=CycleId("a" * 64),
        agent_family_id=family,
        primary_decision=kind,
        requested_action=None if no_action else kind,
        question="Does the cited evidence justify this bounded research action?",
        summary="The action remains research-only and cannot mutate broker state.",
        reason="no_eligible_action" if no_action else None,
        continuation="Wait for new source evidence before another decision." if no_action else None,
        open_work_ref=None,
        subject_refs=subject_refs,
        evidence_refs=("e" * 64,),
        decided_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        model_id="fixture-model-v1",
        prompt_sha256="f" * 64,
        response_sha256="1" * 64,
    )


def _evidence(family: AgentFamilyId) -> ResearchAgentEvidenceV1:
    payload = '{"status":"ready"}'
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId("b" * 64),
        agent_family_id=family,
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"{family}.subject.001",
        evidence_refs=(digest,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=digest,
        market_id="us_equities",
        bounded_payload_json=payload,
        subject_refs=(f"{family}.subject.001",),
    )


def _context(
    family: AgentFamilyId,
    kind: ResearchAgentDecisionKind,
) -> ResearchAgentActionContext:
    return ResearchAgentActionContext(
        cycle=_cycle(family),
        evidence=(_evidence(family),),
        open_work=(),
        decision=_decision(family, kind),
        observed_at=NOW,
    )


def _config(tmp_path: Path) -> ResearchAgentActionConfig:
    uv = shutil.which("uv")
    assert uv is not None
    ready_input = write_ready_systematic_input_activation(
        tmp_path / "production-input",
        tmp_path / "systematic-input.json",
    )
    systematic = SystematicResearchActionExecutor(
        SystematicResearchActionConfig(
            project_root=PROJECT,
            uv_executable=Path(uv),
            python_executable=Path(sys.executable),
            context=PROJECT / "examples" / "research" / "researcher-context-v1.json",
            response_fixture=PROJECT / "examples" / "research" / "researcher-response-fixture-v1.json",
            hermes_executable=None,
            model_id="fixture-model-v1",
            provider_id="fixture-provider",
            experiment_ledger=tmp_path / "experiment.sqlite3",
            receipt_root=tmp_path / "receipts",
            strategy_root=tmp_path / "strategies",
            manifest_root=tmp_path / "manifests",
            queue_root=tmp_path / "queue",
            input_activation=ready_input.activation_path,
            artifact_root=tmp_path / "experiments",
            review_root=tmp_path / "reviews",
            runs_root=tmp_path / "runs",
            max_runtime_seconds=30.0,
        ),
    )
    return ResearchAgentActionConfig(systematic=systematic)


def test_generated_strategy_action_is_systematic_only(tmp_path: Path) -> None:
    executor = ResearchAgentActionExecutor(_config(tmp_path))

    with pytest.raises(InvalidResearchAgentActionError, match="heavy_experiment_systematic_only"):
        executor.execute(_context("day_trading", ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT))


def test_non_systematic_prose_action_is_rejected(tmp_path: Path) -> None:
    executor = ResearchAgentActionExecutor(_config(tmp_path))

    with pytest.raises(InvalidResearchAgentActionError, match="prose_only_result"):
        executor.execute(_context("market_context", ResearchAgentDecisionKind.RUN_LIGHT_EXPERIMENT))


def test_no_action_remains_a_valid_terminal_without_artifact(tmp_path: Path) -> None:
    executor = ResearchAgentActionExecutor(_config(tmp_path))

    result = executor.execute(_context("market_context", ResearchAgentDecisionKind.NO_ACTION))

    assert result.status is ResearchAgentResultStatus.NO_ACTION
    assert result.reason == "no_eligible_action"
    assert result.artifact_refs == ()


def test_primary_family_action_dispatches_to_configured_client(tmp_path: Path) -> None:
    contexts: list[ResearchAgentActionContext] = []
    primary = RecordingPrimaryActionClient(contexts)
    base = _config(tmp_path)
    executor = ResearchAgentActionExecutor(
        ResearchAgentActionConfig(
            systematic=base.systematic,
            market_context=primary,
        )
    )

    result = executor.execute(_context("market_context", ResearchAgentDecisionKind.PUBLISH_CONTEXT))

    assert result.status is ResearchAgentResultStatus.COMPLETED
    assert len(contexts) == 1
