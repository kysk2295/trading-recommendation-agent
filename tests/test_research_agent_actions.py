from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

import pytest

from tests.research_agent_systematic_input_fixtures import (
    write_ready_systematic_input_activation,
)
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_actions import (
    InvalidResearchAgentActionError,
    ResearchAgentActionConfig,
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
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_systematic import (
    SystematicResearchActionConfig,
    SystematicResearchActionExecutor,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)
PROJECT = Path(__file__).resolve().parents[1]


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
        evidence_refs=("e" * 64,),
        decided_at=NOW,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
        next_wake_at=None,
        model_id="fixture-model-v1",
        prompt_sha256="f" * 64,
        response_sha256="1" * 64,
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
    return ResearchAgentActionConfig(systematic=systematic, verified_trade_signal_refs=frozenset())


def test_generated_strategy_action_is_systematic_only(tmp_path: Path) -> None:
    executor = ResearchAgentActionExecutor(_config(tmp_path))

    with pytest.raises(InvalidResearchAgentActionError, match="heavy_experiment_systematic_only"):
        executor.execute(
            _cycle("day_trading"),
            _decision("day_trading", ResearchAgentDecisionKind.REQUEST_HEAVY_EXPERIMENT),
        )


def test_non_systematic_action_builds_zero_authority_result_without_subprocess(tmp_path: Path) -> None:
    executor = ResearchAgentActionExecutor(_config(tmp_path))

    result = executor.execute(
        _cycle("market_context"),
        _decision("market_context", ResearchAgentDecisionKind.PUBLISH_CONTEXT),
    )

    assert result.order_authority is False
    assert result.lifecycle_authority is False
    assert result.allocation_authority is False
    assert not (tmp_path / "runs").exists()


def test_recommendation_requires_an_existing_verified_trade_signal_reference(tmp_path: Path) -> None:
    executor = ResearchAgentActionExecutor(_config(tmp_path))

    with pytest.raises(InvalidResearchAgentActionError, match="verified_trade_signal_required"):
        executor.execute(
            _cycle("day_trading"),
            _decision("day_trading", ResearchAgentDecisionKind.PUBLISH_RECOMMENDATION),
        )
