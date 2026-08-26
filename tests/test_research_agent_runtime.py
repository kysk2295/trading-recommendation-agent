from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from trading_agent.autonomous_task_models import AutonomousSupervisorTickResult, AutonomousTaskId
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
    MarketId,
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
    ResearchAgentRuntimeServices,
)
from trading_agent.research_agent_sources import (
    ResearchAgentSourceCollectionBatch,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _evidence(
    family: AgentFamilyId, sequence: int, market_id: MarketId = "none"
) -> ResearchAgentEvidenceV1:
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
        market_id=market_id,
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
    def execute_context(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        del context
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


@dataclass(frozen=True, slots=True)
class MarketIsolatedDayActionClient:
    contexts: list[ResearchAgentActionContext]

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        self.contexts.append(context)
        failed = context.cycle.market_id == "us_equities"
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(context.cycle.cycle_id),
            cycle_id=context.cycle.cycle_id,
            agent_family_id=context.cycle.agent_family_id,
            market_id=context.cycle.market_id,
            status=(ResearchAgentResultStatus.FAILED if failed else ResearchAgentResultStatus.COMPLETED),
            question=context.decision.question,
            summary="A market-local Day action completed without cross-market state leakage.",
            reason="us_fixture_failure" if failed else None,
            continuation="Retry only the failed US market evidence." if failed else None,
            evidence_refs=context.decision.evidence_refs,
            artifact_refs=() if failed else (context.evidence[0].payload_sha256,),
            occurred_at=context.observed_at,
            next_wake_kind=context.decision.next_wake_kind,
            next_wake_at=context.decision.next_wake_at,
        )


@dataclass(frozen=True, slots=True)
class RecordingSupervisor:
    evidence: list[ResearchAgentEvidenceV1]

    def close(self) -> None:
        return None

    def tick(
        self,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> AutonomousSupervisorTickResult:
        self.evidence.append(evidence)
        return AutonomousSupervisorTickResult(
            status="waiting",
            task_id=AutonomousTaskId(hashlib.sha256(evidence.evidence_id.encode()).hexdigest()),
            agent_family_id=evidence.agent_family_id,
            market_scope=evidence.market_id,
            model_calls=2,
            next_wake_at=now + dt.timedelta(minutes=5),
        )

    def project_tick(
        self,
        cycle: ResearchAgentCycleV1,
        result: AutonomousSupervisorTickResult,
        now: dt.datetime,
    ) -> ResearchAgentResultV1:
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(cycle.cycle_id),
            cycle_id=cycle.cycle_id,
            agent_family_id=cycle.agent_family_id,
            market_id=cycle.market_id,
            status=ResearchAgentResultStatus.NO_ACTION,
            question="What durable autonomous work should continue for this family?",
            summary="The autonomous task reached a deterministic waiting boundary.",
            reason="autonomous_task_waiting",
            continuation="Resume the durable autonomous task at its scheduled wake.",
            open_work_ref=str(result.task_id),
            evidence_refs=(cycle.evidence_id,),
            artifact_refs=(),
            occurred_at=now,
            next_wake_kind=ResearchAgentWakeKind.SCHEDULED,
            next_wake_at=result.next_wake_at,
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


@pytest.mark.parametrize("family", PRIMARY_AGENT_FAMILIES)
def test_every_family_delegates_to_supervisor_before_legacy_decision(
    tmp_path: Path,
    family: AgentFamilyId,
) -> None:
    # Given: one admissible evidence record and an installed persistent supervisor.
    calls: list[AgentFamilyId] = []
    delegated: list[ResearchAgentEvidenceV1] = []
    actions: list[ResearchAgentActionContext] = []
    store = ResearchAgentCycleStore(tmp_path / "cycles.sqlite3")
    runtime = ResearchAgentRuntime(
        ResearchAgentRuntimeServices(
            store,
            EMPTY_COLLECTOR,
            RecordingDecisionClient(calls),
            RecordingArtifactActionClient(actions),
            supervisor_runtime=RecordingSupervisor(delegated),
        )
    )
    runtime.ingest((_evidence(family, 1, "us_equities"),))

    # When: the family cycle reaches the delegation boundary.
    tick = runtime.tick(NOW + dt.timedelta(minutes=2))
    runtime.close()

    # Then: the supervisor owns the tick and neither legacy client is called.
    assert tick.status == "no_action"
    assert tick.model_calls == 2
    assert tuple(item.agent_family_id for item in delegated) == (family,)
    assert calls == []
    assert actions == []
