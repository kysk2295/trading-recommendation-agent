from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from run_research_agent_runtime import main
from tests.test_research_agent_service_cli import _config
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_actions import ResearchAgentActionConfig, ResearchAgentActionExecutor
from trading_agent.research_agent_cycle_models import (
    DecisionId,
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_decision import ResearchAgentDecisionRequest
from trading_agent.research_agent_runtime import (
    ConfiguredResearchAgentEvidenceCollector,
    ResearchAgentRuntime,
    ResearchAgentRuntimeServices,
)
from trading_agent.research_agent_service_config import write_research_agent_service_config

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
            evidence_refs=tuple(sorted({ref for item in request.evidence for ref in item.evidence_refs})),
            decided_at=request.requested_at,
            next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
            next_wake_at=None,
            model_id="fixture-seed-v1",
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
        )


@dataclass(frozen=True, slots=True)
class UnreachableSystematicAction:
    def execute(
        self,
        cycle: ResearchAgentCycleV1,
        decision: ResearchAgentDecisionV1,
    ) -> ResearchAgentResultV1:
        del cycle, decision
        raise AssertionError


def test_idle_service_tick_reports_zero_model_and_broker_mutations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    calls: list[AgentFamilyId] = []
    seed = ResearchAgentRuntime(
        ResearchAgentRuntimeServices(
            store=ResearchAgentCycleStore(config.cycle_database),
            collector=ConfiguredResearchAgentEvidenceCollector(config.source_paths),
            decisions=HypothesisDecisionClient(calls),
            actions=ResearchAgentActionExecutor(
                ResearchAgentActionConfig(
                    systematic=UnreachableSystematicAction(),
                    verified_trade_signal_refs=frozenset(),
                )
            ),
        )
    )
    for _ in range(5):
        assert seed.tick(NOW).status == "completed"
    qa_time = NOW + dt.timedelta(minutes=2, seconds=30)
    for _ in range(8):
        if seed.tick(qa_time).status == "idle":
            break
    else:
        raise AssertionError
    seed.close()
    config_path = (tmp_path / "private" / "runtime.json").absolute()
    assert write_research_agent_service_config(config_path, config)

    seeded_calls = len(calls)
    code = main(("tick", "--config", str(config_path)), clock=lambda: qa_time)

    assert code == 0
    assert len(calls) == seeded_calls
    captured = capsys.readouterr().out
    assert '"status":"idle"' in captured
    assert '"model_calls":0' in captured
    assert '"broker_mutation":0' in captured
    assert f'"projected_results":{seeded_calls}' in captured
