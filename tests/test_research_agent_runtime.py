from __future__ import annotations

import datetime as dt
import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_actions import ResearchAgentActionConfig, ResearchAgentActionExecutor
from trading_agent.research_agent_cycle_models import (
    DecisionId,
    EvidenceId,
    ResearchAgentCycleV1,
    ResearchAgentDecisionKind,
    ResearchAgentDecisionV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_decision import ResearchAgentDecisionRequest
from trading_agent.research_agent_runtime import (
    ResearchAgentRuntime,
    ResearchAgentRuntimeLeaseUnavailableError,
    ResearchAgentRuntimeServices,
    research_agent_runtime_lease,
)
from trading_agent.research_agent_sources import (
    ResearchAgentSourceCollectionBatch,
    ResearchAgentSourceFailure,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _evidence(family: AgentFamilyId, sequence: int) -> ResearchAgentEvidenceV1:
    digest = hashlib.sha256(f"{family}:{sequence}".encode()).hexdigest()
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId(digest),
        agent_family_id=family,
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"runtime.{family}.{sequence}",
        evidence_refs=(digest,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=digest,
        market_id="none",
    )


@dataclass(frozen=True, slots=True)
class StaticCollector:
    batch: ResearchAgentSourceCollectionBatch

    def collect(self, now: dt.datetime) -> ResearchAgentSourceCollectionBatch:
        del now
        return self.batch


@dataclass(frozen=True, slots=True)
class RecordingDecisionClient:
    calls: list[AgentFamilyId]

    def decide(self, request: ResearchAgentDecisionRequest) -> ResearchAgentDecisionV1:
        self.calls.append(request.agent_family_id)
        return ResearchAgentDecisionV1(
            decision_id=DecisionId(hashlib.sha256(f"{request.cycle_id}:decision".encode()).hexdigest()),
            cycle_id=request.cycle_id,
            agent_family_id=request.agent_family_id,
            primary_decision=ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS,
            requested_action=ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS,
            question="Does the current cited evidence support a bounded hypothesis?",
            summary="The evidence supports one research-only hypothesis for review.",
            reason=None,
            continuation=None,
            open_work_ref=None,
            evidence_refs=tuple(sorted({ref for item in request.evidence for ref in item.evidence_refs})),
            decided_at=request.requested_at,
            next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
            next_wake_at=None,
            model_id="fixture-runtime-v1",
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


def _runtime(
    path: Path,
    collector: StaticCollector,
    calls: list[AgentFamilyId],
) -> ResearchAgentRuntime:
    store = ResearchAgentCycleStore(path)
    actions = ResearchAgentActionExecutor(
        ResearchAgentActionConfig(
            systematic=UnreachableSystematicAction(),
            verified_trade_signal_refs=frozenset(),
        )
    )
    return ResearchAgentRuntime(ResearchAgentRuntimeServices(store, collector, RecordingDecisionClient(calls), actions))


def test_idle_ticks_do_not_call_the_model(tmp_path: Path) -> None:
    calls: list[AgentFamilyId] = []
    runtime = _runtime(
        tmp_path / "cycles.sqlite3",
        StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(), failures=())),
        calls,
    )

    first = runtime.tick(NOW)
    second = runtime.tick(NOW + dt.timedelta(seconds=30))
    runtime.close()

    assert first.status == second.status == "idle"
    assert first.model_calls == second.model_calls == 0
    assert calls == []


def test_two_families_run_separate_cycles_and_restart_without_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    calls: list[AgentFamilyId] = []
    runtime = _runtime(
        path,
        StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(), failures=())),
        calls,
    )
    runtime.ingest((_evidence("opportunity_manager", 1), _evidence("systematic_quant", 1)))

    first = runtime.tick(NOW + dt.timedelta(minutes=2))
    second = runtime.tick(NOW + dt.timedelta(minutes=2, seconds=30))
    runtime.close()
    restarted = _runtime(
        path,
        StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(), failures=())),
        calls,
    )
    third = restarted.tick(NOW + dt.timedelta(minutes=3))
    results = restarted.store.results()
    restarted.close()

    assert {first.agent_family_id, second.agent_family_id} == {"opportunity_manager", "systematic_quant"}
    assert third.status == "idle"
    assert len(results) == 2
    assert calls == ["systematic_quant", "opportunity_manager"]


def test_source_failure_is_isolated_and_never_calls_the_model(tmp_path: Path) -> None:
    calls: list[AgentFamilyId] = []
    collector = StaticCollector(
        ResearchAgentSourceCollectionBatch(
            evidence=(),
            failures=(
                ResearchAgentSourceFailure(
                    agent_family_id="market_context",
                    reason="market_context_source_invalid",
                    observed_at=NOW,
                ),
            ),
        )
    )
    runtime = _runtime(tmp_path / "cycles.sqlite3", collector, calls)

    result = runtime.tick(NOW)
    stored = runtime.store.results()
    runtime.close()

    assert result.status == "failed"
    assert result.agent_family_id == "market_context"
    assert result.model_calls == 0
    assert stored[0].reason == "market_context_source_invalid"
    assert calls == []


def test_interrupted_cycle_replays_once_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    evidence = _evidence("swing_trading", 1)
    seed = ResearchAgentCycleStore(path)
    assert seed.append_evidence(evidence)
    stored = seed.runnable_evidence("swing_trading", NOW)
    interrupted = seed.start_cycle(stored[0], NOW)
    seed.close()
    calls: list[AgentFamilyId] = []
    runtime = _runtime(
        path,
        StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(), failures=())),
        calls,
    )

    replay = runtime.tick(NOW + dt.timedelta(minutes=1))
    idle = runtime.tick(NOW + dt.timedelta(minutes=1, seconds=30))
    events = runtime.store.cycle_events(interrupted.cycle_id)
    runtime.close()

    assert replay.recovered_cycles == 1
    assert replay.agent_family_id == "swing_trading"
    assert idle.status == "idle"
    assert [event.state.value for event in events] == ["started", "interrupted", "started", "completed"]
    assert calls == ["swing_trading"]


def test_runtime_lease_is_private_nonblocking_and_reusable(tmp_path: Path) -> None:
    lease = (tmp_path / "private" / "research-runtime.lock").absolute()

    with research_agent_runtime_lease(lease):
        assert stat.S_IMODE(lease.stat().st_mode) == 0o600
        with pytest.raises(ResearchAgentRuntimeLeaseUnavailableError), research_agent_runtime_lease(lease):
            raise AssertionError

    with research_agent_runtime_lease(lease):
        assert lease.is_file()
