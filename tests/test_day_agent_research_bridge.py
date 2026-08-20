from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.day_strategy_capsule_support import no_signal_source
from trading_agent.critic_agent import DeterministicHypothesisCritic
from trading_agent.day_agent_research_bridge import (
    DayAgentDiscoveryBridgeRequest,
    DayAgentDiscoveryBridgeServices,
    DayAgentResearchBridgeError,
    submit_day_agent_hypothesis,
)
from trading_agent.day_agent_task_models import (
    DayAgentAction,
    DayAgentBudget,
    DayAgentResearchTask,
    DayAgentTaskRecordKind,
    DayAgentTaskState,
    DayAgentTaskStep,
)
from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_agent_tool_models import DayAgentHypothesisSubmission
from trading_agent.day_discovery_loop import (
    DayDiscoveryEvidenceView,
    DayDiscoveryLoop,
    DayDiscoveryLoopConfig,
    DayDiscoveryTriggerKind,
    _proposal_semantic_hash,
)
from trading_agent.experiment_ledger_models import ResearchSource, ResearchSourceKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.lane_identity_models import LaneId
from trading_agent.models import BarInput
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import FailureDigest, LlmCallReceipt, ResearcherContext
from trading_agent.researcher_pipeline import (
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore

SUBMITTED_AT = dt.datetime(2026, 8, 21, 14, 30, tzinfo=dt.UTC)
NEXT_BAR_AT = SUBMITTED_AT + dt.timedelta(minutes=1)


def _submission(**updates: object) -> DayAgentHypothesisSubmission:
    payload: dict[str, object] = {
        "hypothesis": "A completed-bar liquidity echo predicts bounded next-bar continuation.",
        "mechanism": "Delayed participation after a liquidity shock may sustain demand for one bar.",
        "baseline": "Matched completed bars without the preregistered liquidity echo.",
        "falsification_conditions": ("Net continuation is not positive on the sealed holdout.",),
        "evidence_refs": ("academic-liquidity-echo",),
        "free_parameters": ("relative_volume_floor",),
        "data_requests": ("completed_bar_v1", "fresh_spread_v1"),
        "experiment_code": no_signal_source(),
        "reason": "The cited mechanism is falsifiable with the bounded completed-bar evidence.",
    }
    payload.update(updates)
    return DayAgentHypothesisSubmission.model_validate(payload)


def _source() -> ResearchSource:
    return ResearchSource(
        source_id="academic-liquidity-echo",
        source_kind=ResearchSourceKind.ACADEMIC_PAPER,
        title="Liquidity shocks and bounded continuation",
        source_url="https://example.org/research/liquidity-echo",
        published_on=dt.date(2020, 1, 2),
        claim="Liquidity shocks can motivate a falsifiable short-horizon continuation test.",
        limitations="The source does not establish profitability or current-session implementability.",
        retrieved_at=SUBMITTED_AT - dt.timedelta(days=1),
        ledger_recorded_at=SUBMITTED_AT - dt.timedelta(days=1),
    )


def _task() -> DayAgentResearchTask:
    return DayAgentResearchTask(
        task_id="task-20260821-liquidity-echo",
        objective="Test one bounded liquidity-echo mechanism.",
        question="Does the cited mechanism warrant future-only shadow research?",
        state=DayAgentTaskState.OPEN,
        evidence_refs=("academic-liquidity-echo",),
        budget=DayAgentBudget(
            remaining_model_calls=1,
            remaining_tool_calls=1,
            remaining_runtime_seconds=60,
        ),
        created_at=SUBMITTED_AT,
        updated_at=SUBMITTED_AT,
    )


def _persisted_request(root: Path, submission: DayAgentHypothesisSubmission) -> DayAgentDiscoveryBridgeRequest:
    task_store = DayAgentTaskStore(root / "day-agent.sqlite3")
    task = _task()
    payload_json = json.dumps(
        submission.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    decision = DayAgentTaskStep(
        task_id=task.task_id,
        sequence=1,
        record_kind=DayAgentTaskRecordKind.DECISION,
        payload_json=payload_json,
        action=DayAgentAction.SUBMIT_RESEARCH_HYPOTHESIS,
        reason=submission.reason,
        evidence_refs=task.evidence_refs,
        budget=task.budget,
        state=DayAgentTaskState.WAITING,
        occurred_at=SUBMITTED_AT,
        scheduled_wake_at=SUBMITTED_AT + dt.timedelta(seconds=1),
    )
    with task_store.writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(decision)
    persisted_task = task_store.reader().task(task.task_id)
    assert persisted_task is not None
    return DayAgentDiscoveryBridgeRequest(
        task=persisted_task,
        decision_step=decision,
        llm_receipt=LlmCallReceipt(
            model_id="day-agent-coder-v1",
            prompt_sha256="a" * 64,
            response_sha256=hashlib.sha256(payload_json.encode()).hexdigest(),
            seed=7,
            temperature=0.0,
            called_at=SUBMITTED_AT,
        ),
        agent_version="day-agent-v1",
        champion_version="champion-2026-08-21",
    )


def accepted_hypothesis_submission(root: Path) -> DayAgentDiscoveryBridgeRequest:
    return _persisted_request(root, _submission())


def _view() -> DayDiscoveryEvidenceView:
    return DayDiscoveryEvidenceView(
        market_id=MarketId.US_EQUITIES,
        trigger_kind=DayDiscoveryTriggerKind.COMPLETED_BAR,
        observed_at=SUBMITTED_AT,
        completed_bar_at=SUBMITTED_AT,
        first_eligible_completed_bar_at=NEXT_BAR_AT,
        universe_snapshot_id="us-universe-20260821-1430",
        universe_snapshot_at=SUBMITTED_AT - dt.timedelta(minutes=2),
        source_refs=("evidence:point-in-time:test",),
        evidence_schema=("completed_bar_v1", "fresh_spread_v1"),
        data_manifest_sha256="d" * 64,
        replay_bars=(
            BarInput(
                "TEST",
                SUBMITTED_AT - dt.timedelta(minutes=1),
                10.0,
                11.0,
                9.5,
                10.5,
                100_000,
                9.8,
                1_000_000,
                20.0,
            ),
        ),
        budget_epoch_ref="us-equities-2026-08-21",
        search_budget=4,
    )


def discovery_bridge_services(root: Path) -> DayAgentDiscoveryBridgeServices:
    runtime = resolve_generated_strategy_runtime(Path(sys.executable))
    ledger = ExperimentLedgerStore(root / "ledger.sqlite3")
    pipeline = ResearcherPipeline(
        ResearcherPipelineServices(
            generator=_NeverCalledGenerator(),
            critic=DeterministicHypothesisCritic(max_free_parameters=4),
        ),
        ResearcherPipelineStores(
            ledger=ledger,
            receipts=ResearcherReceiptStore(root / "receipts"),
            strategies=GeneratedStrategyArtifactStore(root / "artifacts", runtime),
        ),
        ResearcherPipelineArtifacts(root / "manifests", root / "queue"),
    )
    return DayAgentDiscoveryBridgeServices(
        task_store=DayAgentTaskStore(root / "day-agent.sqlite3"),
        discovery_loop=DayDiscoveryLoop(
            DayDiscoveryLoopConfig(
                pipeline=pipeline,
                sandbox=GeneratedStrategySandbox(runtime, root / "sandbox", _view().resource_limits),
                max_drafts=1,
            )
        ),
        evidence_view=_view(),
        researcher_context=ResearcherContext(
            lane_id=LaneId.INTRADAY_MOMENTUM,
            sources=(_source(),),
            failure_digest=FailureDigest((), (), ()),
            regime_context="bounded current-session completed-bar evidence",
            existing_hypothesis_texts=(),
        ),
    )


class _NeverCalledGenerator:
    def propose(self, context: ResearcherContext):
        del context
        raise AssertionError("the bridge must replace the host generator with FixedHypothesisGenerator")


def test_persisted_submission_publishes_one_future_only_research_capsule(tmp_path: Path) -> None:
    request = accepted_hypothesis_submission(tmp_path)
    services = discovery_bridge_services(tmp_path)

    result = submit_day_agent_hypothesis(request, services)

    assert result.accepted is True
    assert result.capsule_id is not None
    assert result.first_shadow_eligible_at > request.submitted_at
    assert result.order_authority is False
    reader = services.discovery_loop.config.pipeline.stores.ledger.reader()
    assert len(reader.day_hypothesis_versions(market_id=MarketId.US_EQUITIES)) == 1
    state = reader.day_discovery_cycle_state(result.cycle_id)
    assert sum(event.event_kind.value == "call_response_recorded" for event in state.events) == 1
    version = reader.day_hypothesis_versions(market_id=MarketId.US_EQUITIES)[0].version
    assert version.prompt_sha256 == request.llm_receipt.prompt_sha256
    assert request.task.task_id in version.source_refs
    assert request.decision_step.step_id in version.source_refs
    assert request.llm_receipt.response_sha256 in version.source_refs
    assert request.llm_receipt.model_id in version.source_refs
    assert request.agent_version in version.source_refs
    assert request.champion_version in version.source_refs
    capsule = reader.day_strategy_capsules(MarketId.US_EQUITIES)[0].capsule
    assert capsule.authority_ceiling.value == "research_only"


@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        ({"data_requests": ("private_future_feed",)}, "day_agent_data_request_unverifiable"),
        (
            {"experiment_code": "import os\ndef create_strategy(context):\n return os.system('true')"},
            "day_agent_python_unsafe",
        ),
    ),
)
def test_bridge_rejects_unverifiable_data_and_unsafe_python(
    tmp_path: Path,
    updates: dict[str, object],
    reason: str,
) -> None:
    request = _persisted_request(tmp_path, _submission(**updates))

    with pytest.raises(DayAgentResearchBridgeError, match=reason):
        submit_day_agent_hypothesis(request, discovery_bridge_services(tmp_path))


def test_bridge_rejects_semantic_duplicate_before_discovery(tmp_path: Path) -> None:
    request = accepted_hypothesis_submission(tmp_path)
    services = discovery_bridge_services(tmp_path)
    proposal = services.proposal_for(request)
    duplicate_services = replace(
        services,
        evidence_view=services.evidence_view.model_copy(
            update={"existing_semantic_hashes": (_proposal_semantic_hash(proposal),)}
        ),
    )

    result = submit_day_agent_hypothesis(request, duplicate_services)

    assert result.accepted is False
    assert result.terminal_reason == "semantic_duplicate"
    assert result.capsule_id is None


def test_hypothesis_submission_requires_citations_and_at_most_four_parameters() -> None:
    with pytest.raises(ValidationError):
        _submission(evidence_refs=())
    with pytest.raises(ValidationError):
        _submission(free_parameters=("a", "b", "c", "d", "e"))
