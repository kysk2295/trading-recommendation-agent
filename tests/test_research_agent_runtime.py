from __future__ import annotations

import datetime as dt
import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.research_agent_actions import (
    ResearchAgentActionClient,
    ResearchAgentActionConfig,
    ResearchAgentActionContext,
    ResearchAgentActionExecutor,
)
from trading_agent.research_agent_cycle_models import (
    DecisionId,
    EvidenceId,
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
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_decision import ResearchAgentDecisionRequest
from trading_agent.research_agent_runtime import (
    ResearchAgentRuntime,
    ResearchAgentRuntimeLeaseUnavailableError,
    ResearchAgentRuntimeServices,
    research_agent_runtime_lease,
)
from trading_agent.research_agent_runtime_support import scheduled_evidence
from trading_agent.research_agent_sources import (
    ResearchAgentSourceCollectionBatch,
    ResearchAgentSourceFailure,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _evidence(family: AgentFamilyId, sequence: int) -> ResearchAgentEvidenceV1:
    payload = f'{{"sequence":{sequence}}}'
    digest = hashlib.sha256(payload.encode()).hexdigest()
    identity = hashlib.sha256(f"{family}:{sequence}".encode()).hexdigest()
    source_key = f"runtime.{family}.{sequence}"
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId(identity),
        agent_family_id=family,
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key=source_key,
        evidence_refs=(digest,),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=digest,
        market_id="none",
        bounded_payload_json=payload,
        subject_refs=(source_key,),
    )


@dataclass(frozen=True, slots=True)
class StaticCollector:
    batch: ResearchAgentSourceCollectionBatch

    def collect(self, now: dt.datetime) -> ResearchAgentSourceCollectionBatch:
        del now
        return self.batch


EMPTY_COLLECTOR = StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(), failures=()))


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
            subject_refs=request.evidence[0].subject_refs,
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


@dataclass(frozen=True, slots=True)
class RecordingArtifactActionClient:
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
            summary="A deterministic fixture artifact was recorded for runtime contract testing.",
            reason=None,
            continuation=None,
            evidence_refs=context.decision.evidence_refs,
            artifact_refs=(context.evidence[0].payload_sha256,),
            occurred_at=context.observed_at,
            next_wake_kind=context.decision.next_wake_kind,
            next_wake_at=context.decision.next_wake_at,
        )


def _runtime(
    path: Path,
    collector: StaticCollector,
    calls: list[AgentFamilyId],
    actions: ResearchAgentActionClient | None = None,
) -> ResearchAgentRuntime:
    store = ResearchAgentCycleStore(path)
    action_client = actions or RecordingArtifactActionClient([])
    return ResearchAgentRuntime(
        ResearchAgentRuntimeServices(store, collector, RecordingDecisionClient(calls), action_client)
    )


def _production_actions() -> ResearchAgentActionExecutor:
    return ResearchAgentActionExecutor(ResearchAgentActionConfig(systematic=UnreachableSystematicAction()))


def test_runtime_passes_action_context_with_evidence_subjects_and_observation_time(tmp_path: Path) -> None:
    contexts: list[ResearchAgentActionContext] = []
    runtime = _runtime(
        tmp_path / "cycles.sqlite3",
        EMPTY_COLLECTOR,
        [],
        RecordingArtifactActionClient(contexts),
    )
    runtime.ingest((_evidence("swing_trading", 1),))

    tick = runtime.tick(NOW + dt.timedelta(minutes=2))
    stored = runtime.store.results()
    runtime.close()

    assert tick.status == "completed"
    assert stored[0].decision_kind is ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS
    assert contexts[0].evidence[0].bounded_payload_json == '{"sequence":1}'
    assert contexts[0].decision.subject_refs == contexts[0].evidence[0].subject_refs
    assert contexts[0].observed_at == NOW + dt.timedelta(minutes=2)


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


def test_bounded_cycle_processes_each_family_once_and_replay_is_idle(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    calls: list[AgentFamilyId] = []
    runtime = _runtime(path, EMPTY_COLLECTOR, calls)
    runtime.ingest(tuple(_evidence(family, 1) for family in PRIMARY_AGENT_FAMILIES))

    first = runtime.cycle(NOW + dt.timedelta(minutes=2))
    cursors = tuple(runtime.store.cursor(family) for family in PRIMARY_AGENT_FAMILIES)
    open_work = tuple(runtime.store.open_work(family) for family in PRIMARY_AGENT_FAMILIES)
    runtime.close()
    restarted = _runtime(path, EMPTY_COLLECTOR, calls)
    replay = restarted.cycle(NOW + dt.timedelta(minutes=3))
    results = restarted.store.results()
    restarted.close()

    assert first.status == "complete"
    assert tuple(item.agent_family_id for item in first.outcomes) == PRIMARY_AGENT_FAMILIES
    assert first.model_calls == 6
    assert first.recovered_cycles == 0
    assert all(cursor > 0 for cursor in cursors)
    assert all(len(items) == 1 and items[0].state.value == "terminal" for items in open_work)
    assert replay.status == "idle"
    assert replay.outcomes == ()
    assert len(results) == 6
    assert calls == list(PRIMARY_AGENT_FAMILIES)


def test_bounded_cycle_does_not_debounce_fresh_one_minute_opportunity_past_expiry(
    tmp_path: Path,
) -> None:
    calls: list[AgentFamilyId] = []
    runtime = _runtime(tmp_path / "cycles.sqlite3", EMPTY_COLLECTOR, calls)
    runtime.ingest(tuple(_evidence(family, 1) for family in PRIMARY_AGENT_FAMILIES))

    cycle = runtime.cycle(NOW + dt.timedelta(seconds=30))
    runtime.close()

    assert cycle.status == "complete"
    assert tuple(item.agent_family_id for item in cycle.outcomes) == PRIMARY_AGENT_FAMILIES


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


@pytest.mark.parametrize(
    ("family", "source_key"),
    (
        ("opportunity_manager", "opportunity.blocked.snapshot_unavailable"),
        ("market_context", "market_context.blocked.snapshot_unavailable"),
        ("day_trading", "day.blocked.completed_bar_unavailable"),
    ),
)
def test_primary_blocked_evidence_persists_no_action_before_model_call(
    tmp_path: Path,
    family: AgentFamilyId,
    source_key: str,
) -> None:
    # Given: a Primary source admitted explicit blocked evidence.
    calls: list[AgentFamilyId] = []
    runtime = _runtime(tmp_path / "cycles.sqlite3", EMPTY_COLLECTOR, calls)
    runtime.ingest((_evidence(family, 1).model_copy(update={"source_key": source_key}),))

    # When: the runtime evaluates that evidence.
    tick = runtime.tick(NOW + dt.timedelta(minutes=2))
    stored = runtime.store.results()
    runtime.close()

    # Then: a deterministic terminal no-action is persisted without model or publication artifacts.
    assert tick.status == "no_action"
    assert tick.agent_family_id == family
    assert tick.model_calls == 0
    assert len(stored) == 1
    assert stored[0].reason == source_key
    assert stored[0].continuation == "Wait for current-session Primary evidence that passes source admission."
    assert stored[0].next_wake_kind is ResearchAgentWakeKind.NEW_EVIDENCE
    assert stored[0].next_wake_at is None
    assert stored[0].artifact_refs == ()
    assert calls == []


def test_closed_session_primary_schedule_persists_no_action_before_model_call(tmp_path: Path) -> None:
    # Given: a Primary scheduled wake is selected while the New York regular session is closed.
    calls: list[AgentFamilyId] = []
    runtime = _runtime(tmp_path / "cycles.sqlite3", EMPTY_COLLECTOR, calls)
    runtime.ingest((scheduled_evidence("market_context", NOW, 30),))

    # When: the runtime evaluates scheduled work on Sunday.
    tick = runtime.tick(NOW)
    stored = runtime.store.results()
    runtime.close()

    # Then: it waits for Monday's regular open without invoking the model or publishing artifacts.
    assert tick.status == "no_action"
    assert tick.agent_family_id == "market_context"
    assert tick.model_calls == 0
    assert stored[0].reason == "market_context.regular_session_closed"
    assert stored[0].continuation == "Wait until the next New York regular session."
    assert stored[0].next_wake_kind is ResearchAgentWakeKind.SCHEDULED
    assert stored[0].next_wake_at == dt.datetime(2026, 8, 3, 13, 30, tzinfo=dt.UTC)
    assert stored[0].artifact_refs == ()
    assert calls == []


def test_research_blocked_evidence_rejects_prose_only_completion(tmp_path: Path) -> None:
    # Given: a Research-family source admitted explicit blocked evidence.
    calls: list[AgentFamilyId] = []
    evidence = _evidence("swing_trading", 1).model_copy(update={"source_key": "swing.blocked.shadow_evidence_empty"})
    runtime = _runtime(tmp_path / "cycles.sqlite3", EMPTY_COLLECTOR, calls, _production_actions())
    runtime.ingest((evidence,))

    # When: the runtime evaluates that evidence.
    tick = runtime.tick(NOW)
    stored = runtime.store.results()
    runtime.close()

    assert tick.status == "failed"
    assert tick.model_calls == 1
    assert stored[0].reason == "prose_only_result"
    assert stored[0].artifact_refs == ()
    assert calls == ["swing_trading"]


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
