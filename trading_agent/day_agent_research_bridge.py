from __future__ import annotations

import ast
import datetime as dt
import hashlib
import re
from dataclasses import dataclass, replace
from typing import Literal, override

from pydantic import TypeAdapter, ValidationError

from trading_agent.day_agent_task_models import (
    DayAgentAction,
    DayAgentResearchTask,
    DayAgentTaskRecordKind,
    DayAgentTaskStep,
)
from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_agent_tool_models import DayAgentHypothesisSubmission
from trading_agent.day_discovery_loop import (
    DayDiscoveryCycleResult,
    DayDiscoveryEvidenceView,
    DayDiscoveryLoop,
)
from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
)
from trading_agent.experiment_scope_models import ExperimentScope, ExperimentScopeKind
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_identity_models import LaneId
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import (
    CandidateStrategyDraft,
    FixedHypothesisGenerator,
    LlmCallReceipt,
    ProposedHypothesis,
    ResearcherContext,
)

_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_SUBMISSION_ADAPTER = TypeAdapter(DayAgentHypothesisSubmission)
_FORBIDDEN_CALLS = frozenset({"__import__", "compile", "eval", "exec", "open"})


class DayAgentResearchBridgeError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class DayAgentDiscoveryBridgeRequest:
    task: DayAgentResearchTask
    decision_step: DayAgentTaskStep
    llm_receipt: LlmCallReceipt
    agent_version: str
    champion_version: str

    @property
    def submitted_at(self) -> dt.datetime:
        return self.decision_step.occurred_at


@dataclass(frozen=True, slots=True)
class DayAgentDiscoveryBridgeResult:
    accepted: bool
    capsule_id: str | None
    first_shadow_eligible_at: dt.datetime
    terminal_reason: str | None
    cycle_id: str
    hypothesis_version_id: str | None
    order_authority: Literal[False] = False


@dataclass(frozen=True, slots=True)
class DayAgentDiscoveryBridgeServices:
    task_store: DayAgentTaskStore
    discovery_loop: DayDiscoveryLoop
    evidence_view: DayDiscoveryEvidenceView
    researcher_context: ResearcherContext

    def proposal_for(self, request: DayAgentDiscoveryBridgeRequest) -> ProposedHypothesis:
        submission = _validated_submission(request, self)
        return _proposal(request, submission, self.researcher_context.sources)


def submit_day_agent_hypothesis(
    request: DayAgentDiscoveryBridgeRequest,
    services: DayAgentDiscoveryBridgeServices,
) -> DayAgentDiscoveryBridgeResult:
    proposal = services.proposal_for(request)
    view = _lineage_view(request, proposal, services.evidence_view)
    context = _task_context(request.task, proposal.cited_sources, services.researcher_context)
    base_config = services.discovery_loop.config
    fixed_pipeline = replace(
        base_config.pipeline,
        services=replace(
            base_config.pipeline.services,
            generator=FixedHypothesisGenerator(proposal),
        ),
    )
    result = DayDiscoveryLoop(replace(base_config, pipeline=fixed_pipeline)).run(view, context)
    return _bridge_result(result)


def _validated_submission(
    request: DayAgentDiscoveryBridgeRequest,
    services: DayAgentDiscoveryBridgeServices,
) -> DayAgentHypothesisSubmission:
    step = request.decision_step
    stored_task = services.task_store.reader().task(request.task.task_id)
    stored_steps = services.task_store.reader().steps(request.task.task_id)
    if stored_task != request.task or step not in stored_steps:
        raise DayAgentResearchBridgeError("day_agent_submission_not_persisted")
    if (
        step.task_id != request.task.task_id
        or step.record_kind is not DayAgentTaskRecordKind.DECISION
        or step.action is not DayAgentAction.SUBMIT_RESEARCH_HYPOTHESIS
    ):
        raise DayAgentResearchBridgeError("day_agent_decision_lineage_invalid")
    try:
        submission = _SUBMISSION_ADAPTER.validate_json(step.payload_json)
    except ValidationError:
        raise DayAgentResearchBridgeError("day_agent_submission_invalid") from None
    receipt = request.llm_receipt
    if (
        _HEX64.fullmatch(receipt.prompt_sha256) is None
        or _HEX64.fullmatch(receipt.response_sha256) is None
        or receipt.response_sha256 != hashlib.sha256(step.payload_json.encode()).hexdigest()
        or receipt.called_at.astimezone(dt.UTC) != step.occurred_at
        or not receipt.model_id
    ):
        raise DayAgentResearchBridgeError("day_agent_model_receipt_mismatch")
    if _VERSION.fullmatch(request.agent_version) is None or _VERSION.fullmatch(request.champion_version) is None:
        raise DayAgentResearchBridgeError("day_agent_version_invalid")
    if services.evidence_view.market_id is not MarketId.US_EQUITIES:
        raise DayAgentResearchBridgeError("day_agent_market_invalid")
    if (
        request.submitted_at > services.evidence_view.completed_bar_at
        or request.submitted_at > services.evidence_view.observed_at
        or services.evidence_view.first_eligible_completed_bar_at <= request.submitted_at
    ):
        raise DayAgentResearchBridgeError("day_agent_future_only_boundary_invalid")
    source_ids = {source.source_id for source in services.researcher_context.sources}
    if any(reference not in source_ids for reference in submission.evidence_refs):
        raise DayAgentResearchBridgeError("day_agent_source_citation_unresolved")
    if any(source.retrieved_at > request.submitted_at for source in services.researcher_context.sources):
        raise DayAgentResearchBridgeError("day_agent_source_not_point_in_time")
    if set(submission.data_requests) - set(services.evidence_view.evidence_schema):
        raise DayAgentResearchBridgeError("day_agent_data_request_unverifiable")
    if submission.experiment_code is None or not _safe_python(submission.experiment_code):
        raise DayAgentResearchBridgeError("day_agent_python_unsafe")
    return submission


def _proposal(
    request: DayAgentDiscoveryBridgeRequest,
    submission: DayAgentHypothesisSubmission,
    available_sources: tuple[ResearchSource, ...],
) -> ProposedHypothesis:
    cited = tuple(source for source in available_sources if source.source_id in submission.evidence_refs)
    hypothesis_id = f"day-agent-{hashlib.sha256(request.decision_step.step_id.encode()).hexdigest()[:40]}"
    scope = ExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id=hypothesis_id,
        primary_lane=LaneId.INTRADAY_MOMENTUM,
        lanes=(LaneId.INTRADAY_MOMENTUM,),
        registered_at=request.submitted_at,
    )
    registration = HypothesisRegistration(
        hypothesis_id=hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=experiment_scope_key(scope),
        primary_lane=scope.primary_lane,
        hypothesis=submission.hypothesis,
        falsification_rule=" ".join(submission.falsification_conditions),
        source_registered_at=request.submitted_at,
        ledger_recorded_at=request.submitted_at,
    )
    card = ResearchHypothesisCard(
        hypothesis=registration,
        research_source_keys=tuple(sorted(str(research_source_key(source)) for source in cited)),
        economic_mechanism=submission.mechanism,
        counterfactual_baseline=submission.baseline,
    )
    methodology_tags = tuple(
        sorted(
            {
                "day_agent_submitted",
                f"agent_{request.agent_version}",
                f"champion_{request.champion_version}",
            }
        )
    )
    return ProposedHypothesis(
        card=card,
        cited_sources=cited,
        llm_receipt=request.llm_receipt,
        strategy_draft=CandidateStrategyDraft(
            source_code=submission.experiment_code or "",
            free_parameters=submission.free_parameters,
            methodology_tags=methodology_tags,
        ),
    )


def _lineage_view(
    request: DayAgentDiscoveryBridgeRequest,
    proposal: ProposedHypothesis,
    view: DayDiscoveryEvidenceView,
) -> DayDiscoveryEvidenceView:
    receipt = request.llm_receipt
    lineage = {
        *view.source_refs,
        *(source.source_id for source in proposal.cited_sources),
        request.task.task_id,
        request.decision_step.step_id,
        receipt.prompt_sha256,
        receipt.response_sha256,
        receipt.model_id,
        request.agent_version,
        request.champion_version,
    }
    return view.model_copy(update={"source_refs": tuple(sorted(lineage))})


def _task_context(
    task: DayAgentResearchTask,
    sources: tuple[ResearchSource, ...],
    base: ResearcherContext,
) -> ResearcherContext:
    return replace(
        base,
        lane_id=LaneId.INTRADAY_MOMENTUM,
        sources=sources,
        regime_context=f"{task.objective} {task.question}",
    )


def _safe_python(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            return False
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_CALLS:
            return False
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            return False
    return True


def _bridge_result(result: DayDiscoveryCycleResult) -> DayAgentDiscoveryBridgeResult:
    return DayAgentDiscoveryBridgeResult(
        accepted=result.accepted,
        capsule_id=result.capsule_id,
        first_shadow_eligible_at=result.first_eligible_completed_bar_at,
        terminal_reason=result.terminal_reason,
        cycle_id=result.cycle_id,
        hypothesis_version_id=result.hypothesis_version_id,
    )


__all__ = (
    "DayAgentDiscoveryBridgeRequest",
    "DayAgentDiscoveryBridgeResult",
    "DayAgentDiscoveryBridgeServices",
    "DayAgentResearchBridgeError",
    "submit_day_agent_hypothesis",
)
