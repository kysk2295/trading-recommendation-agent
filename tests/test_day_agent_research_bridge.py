from __future__ import annotations

import datetime as dt
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
    publish_day_agent_research_lineage_binding,
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
)
from trading_agent.experiment_ledger_models import ResearchSource, ResearchSourceKind
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.generated_strategy_runtime import resolve_generated_strategy_runtime
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.lane_identity_models import LaneId
from trading_agent.models import BarInput
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import FailureDigest, ResearcherContext
from trading_agent.researcher_pipeline import (
    ResearcherPipeline,
    ResearcherPipelineArtifacts,
    ResearcherPipelineServices,
    ResearcherPipelineStores,
)
from trading_agent.researcher_receipt_store import ResearcherReceiptStore

COMPLETED_BAR_AT = dt.datetime(2026, 8, 21, 14, 30, tzinfo=dt.UTC)
SUBMITTED_AT = COMPLETED_BAR_AT + dt.timedelta(seconds=10)
NEXT_BAR_AT = COMPLETED_BAR_AT + dt.timedelta(minutes=1)


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


def _task(
    *,
    task_id: str = "task-20260821-liquidity-echo",
    submitted_at: dt.datetime = SUBMITTED_AT,
) -> DayAgentResearchTask:
    return DayAgentResearchTask(
        task_id=task_id,
        objective="Test one bounded liquidity-echo mechanism.",
        question="Does the cited mechanism warrant future-only shadow research?",
        state=DayAgentTaskState.OPEN,
        evidence_refs=("academic-liquidity-echo",),
        budget=DayAgentBudget(
            remaining_model_calls=1,
            remaining_tool_calls=1,
            remaining_runtime_seconds=60,
        ),
        created_at=submitted_at,
        updated_at=submitted_at,
    )


def _persisted_request(
    root: Path,
    submission: DayAgentHypothesisSubmission,
    *,
    task_id: str = "task-20260821-liquidity-echo",
    submitted_at: dt.datetime = SUBMITTED_AT,
    prompt: str = "bounded day-agent research prompt",
    agent_version: str = "day-agent-v1",
    champion_version: str = "champion-2026-08-21",
) -> DayAgentDiscoveryBridgeRequest:
    task_store = DayAgentTaskStore(root / "day-agent.sqlite3")
    task = _task(task_id=task_id, submitted_at=submitted_at)
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
        occurred_at=submitted_at,
        scheduled_wake_at=submitted_at + dt.timedelta(seconds=1),
    )
    with task_store.writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(decision)
    persisted_task = task_store.reader().task(task.task_id)
    assert persisted_task is not None
    receipt_store = ResearcherReceiptStore(root / "receipts")
    receipt = receipt_store.record_call(
        model_id="day-agent-coder-v1",
        prompt=prompt,
        response=payload_json.encode(),
        seed=7,
        temperature=0.0,
        called_at=submitted_at,
    )
    verified = receipt_store.require_call(receipt)
    binding = publish_day_agent_research_lineage_binding(
        root / "lineage",
        task_id=task.task_id,
        step_id=decision.step_id,
        call_id=verified.record.call_id,
        agent_version=agent_version,
        champion_version=champion_version,
        bound_at=submitted_at,
    )
    return DayAgentDiscoveryBridgeRequest(
        task=persisted_task,
        decision_step=decision,
        llm_receipt=receipt,
        lineage_binding_id=binding.binding_id,
    )


def accepted_hypothesis_submission(root: Path) -> DayAgentDiscoveryBridgeRequest:
    return _persisted_request(root, _submission())


def _view(
    *,
    completed_bar_at: dt.datetime = COMPLETED_BAR_AT,
    observed_at: dt.datetime = COMPLETED_BAR_AT,
    first_eligible_at: dt.datetime = NEXT_BAR_AT,
    cursor: str = "origin",
) -> DayDiscoveryEvidenceView:
    return DayDiscoveryEvidenceView(
        market_id=MarketId.US_EQUITIES,
        trigger_kind=DayDiscoveryTriggerKind.COMPLETED_BAR,
        observed_at=observed_at,
        completed_bar_at=completed_bar_at,
        first_eligible_completed_bar_at=first_eligible_at,
        universe_snapshot_id="us-universe-20260821-1430",
        universe_snapshot_at=completed_bar_at - dt.timedelta(minutes=2),
        source_refs=("evidence:point-in-time:test",),
        evidence_schema=("completed_bar_v1", "fresh_spread_v1"),
        data_manifest_sha256="d" * 64,
        replay_bars=(
            BarInput(
                "TEST",
                completed_bar_at - dt.timedelta(minutes=1),
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
        cursor=cursor,
    )


def discovery_bridge_services(
    root: Path,
    *,
    view: DayDiscoveryEvidenceView | None = None,
) -> DayAgentDiscoveryBridgeServices:
    evidence_view = _view() if view is None else view
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
                sandbox=GeneratedStrategySandbox(runtime, root / "sandbox", evidence_view.resource_limits),
                max_drafts=1,
            )
        ),
        evidence_view=evidence_view,
        receipt_store=ResearcherReceiptStore(root / "receipts"),
        lineage_root=root / "lineage",
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
    binding = services.lineage_binding(request)
    assert binding.agent_version in version.source_refs
    assert binding.champion_version in version.source_refs
    assert binding.call_id in version.source_refs
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
    first = accepted_hypothesis_submission(tmp_path)
    first_services = discovery_bridge_services(tmp_path)
    assert submit_day_agent_hypothesis(first, first_services).accepted is True
    second = _persisted_request(
        tmp_path,
        _submission(reason="A different explanation does not change semantic identity."),
        task_id="task-20260821-liquidity-echo-duplicate",
        submitted_at=SUBMITTED_AT + dt.timedelta(seconds=10),
        prompt="different prompt with the same semantic proposal",
        champion_version="champion-2026-08-22",
    )
    second_view = _view(cursor="second-cursor")

    result = submit_day_agent_hypothesis(
        second,
        discovery_bridge_services(tmp_path, view=second_view),
    )

    assert result.accepted is False
    assert result.terminal_reason == "semantic_duplicate"
    assert result.capsule_id is None
    reader = first_services.discovery_loop.config.pipeline.stores.ledger.reader()
    assert len(reader.day_hypothesis_versions()) == 1
    assert len(reader.day_strategy_capsules(MarketId.US_EQUITIES)) == 1
    state = reader.day_discovery_cycle_state(result.cycle_id)
    assert state.events[-1].event_kind.value == "cycle_finalized"
    branch = next(event for event in state.events if event.event_kind.value == "branch_finalized")
    assert json.loads(branch.payload_json)["terminal_reason"] == "semantic_duplicate"
    prepared = next(event for event in state.events if event.event_kind.value == "branch_prepared")
    prepared_payload = json.loads(prepared.payload_json)
    assert "champion-2026-08-22" in prepared_payload["prepared"]["version"]["source_refs"]
    duplicate_attempts = tuple(
        attempt
        for version in reader.day_hypothesis_versions()
        for attempt in reader.day_attempts_for_review(version.version.market_id, version.version.hypothesis_version_id)
        if attempt.attempt.error_class == "semantic_duplicate"
    )
    assert len(duplicate_attempts) == 1


def test_bridge_rejects_forged_receipt_and_lineage_metadata(tmp_path: Path) -> None:
    request = accepted_hypothesis_submission(tmp_path)
    services = discovery_bridge_services(tmp_path)
    forged_receipt = replace(request.llm_receipt, prompt_sha256="f" * 64)

    with pytest.raises(DayAgentResearchBridgeError, match="day_agent_model_receipt_mismatch"):
        submit_day_agent_hypothesis(replace(request, llm_receipt=forged_receipt), services)
    with pytest.raises(DayAgentResearchBridgeError, match="day_agent_lineage_binding_invalid"):
        submit_day_agent_hypothesis(replace(request, lineage_binding_id="f" * 64), services)


def test_close_submission_rolls_to_next_xnys_first_completed_minute(tmp_path: Path) -> None:
    completed = dt.datetime(2026, 8, 21, 20, 0, tzinfo=dt.UTC)
    submitted = completed + dt.timedelta(seconds=10)
    request = _persisted_request(tmp_path, _submission(), submitted_at=submitted)
    view = _view(
        completed_bar_at=completed,
        observed_at=completed,
        first_eligible_at=completed + dt.timedelta(minutes=1),
    )

    result = submit_day_agent_hypothesis(request, discovery_bridge_services(tmp_path, view=view))

    assert result.accepted is True
    assert result.first_shadow_eligible_at == dt.datetime(2026, 8, 24, 13, 31, tzinfo=dt.UTC)


def test_bridge_rejects_premarket_submission_with_prior_session_close(tmp_path: Path) -> None:
    prior_friday_close = dt.datetime(2026, 8, 21, 20, 0, tzinfo=dt.UTC)
    monday_premarket = dt.datetime(2026, 8, 24, 13, 20, 10, tzinfo=dt.UTC)
    request = _persisted_request(tmp_path, _submission(), submitted_at=monday_premarket)
    view = _view(
        completed_bar_at=prior_friday_close,
        observed_at=prior_friday_close,
        first_eligible_at=monday_premarket + dt.timedelta(minutes=11),
    )

    with pytest.raises(DayAgentResearchBridgeError, match="day_agent_current_session_bar_unavailable"):
        submit_day_agent_hypothesis(request, discovery_bridge_services(tmp_path, view=view))


def test_bridge_rejects_stale_intraday_completed_bar(tmp_path: Path) -> None:
    submitted = dt.datetime(2026, 8, 21, 14, 35, 10, tzinfo=dt.UTC)
    stale_bar = dt.datetime(2026, 8, 21, 14, 30, tzinfo=dt.UTC)
    request = _persisted_request(tmp_path, _submission(), submitted_at=submitted)
    view = _view(
        completed_bar_at=stale_bar,
        observed_at=stale_bar,
        first_eligible_at=submitted.replace(second=0) + dt.timedelta(minutes=1),
    )

    with pytest.raises(DayAgentResearchBridgeError, match="day_agent_completed_bar_not_latest"):
        submit_day_agent_hypothesis(request, discovery_bridge_services(tmp_path, view=view))


def test_bridge_rejects_saturday_completed_bar(tmp_path: Path) -> None:
    saturday = dt.datetime(2026, 8, 22, 14, 30, tzinfo=dt.UTC)
    request = _persisted_request(tmp_path, _submission(), submitted_at=saturday + dt.timedelta(seconds=10))
    view = _view(
        completed_bar_at=saturday,
        observed_at=saturday,
        first_eligible_at=saturday + dt.timedelta(minutes=1),
    )

    with pytest.raises(DayAgentResearchBridgeError, match="day_agent_completed_bar_not_xnys"):
        submit_day_agent_hypothesis(request, discovery_bridge_services(tmp_path, view=view))


def test_hypothesis_submission_requires_citations_and_at_most_four_parameters() -> None:
    with pytest.raises(ValidationError):
        _submission(evidence_refs=())
    with pytest.raises(ValidationError):
        _submission(free_parameters=("a", "b", "c", "d", "e"))
