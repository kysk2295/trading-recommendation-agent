from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, override

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

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
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import (
    CandidateStrategyDraft,
    FixedHypothesisGenerator,
    LlmCallReceipt,
    ProposedHypothesis,
    ResearcherContext,
)
from trading_agent.researcher_receipt_store import (
    ResearcherReceiptStore,
    ResearcherReceiptStoreError,
)
from trading_agent.us_equity_calendar import (
    NEW_YORK,
    UnsupportedUsEquityCalendarDateError,
    next_regular_session,
    regular_session_bounds,
)

_HEX64 = re.compile(r"^[a-f0-9]{64}$")
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


class DayAgentResearchLineageBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    binding_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")
    step_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    call_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    agent_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    champion_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
    bound_at: AwareDatetime

    @field_validator("bound_at", mode="after")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def require_identity(self) -> DayAgentResearchLineageBinding:
        if self.binding_id != _binding_id(self.model_dump(mode="python")):
            raise DayAgentResearchBridgeError("day_agent_lineage_binding_invalid")
        return self


@dataclass(frozen=True, slots=True)
class DayAgentDiscoveryBridgeRequest:
    task: DayAgentResearchTask
    decision_step: DayAgentTaskStep
    llm_receipt: LlmCallReceipt
    lineage_binding_id: str

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
    receipt_store: ResearcherReceiptStore
    lineage_root: Path
    researcher_context: ResearcherContext

    def proposal_for(self, request: DayAgentDiscoveryBridgeRequest) -> ProposedHypothesis:
        binding = self.lineage_binding(request)
        submission = _validated_submission(request, self, binding)
        return _proposal(request, submission, binding, self.researcher_context.sources)

    def lineage_binding(self, request: DayAgentDiscoveryBridgeRequest) -> DayAgentResearchLineageBinding:
        if _HEX64.fullmatch(request.lineage_binding_id) is None:
            raise DayAgentResearchBridgeError("day_agent_lineage_binding_invalid")
        try:
            binding = DayAgentResearchLineageBinding.model_validate_json(
                read_private_text(
                    self.lineage_root / "bindings" / f"{request.lineage_binding_id}.json"
                )
            )
        except (InvalidPrivateImmutableFileError, ValidationError, ValueError):
            raise DayAgentResearchBridgeError("day_agent_lineage_binding_invalid") from None
        if binding.binding_id != request.lineage_binding_id:
            raise DayAgentResearchBridgeError("day_agent_lineage_binding_invalid")
        return binding


def submit_day_agent_hypothesis(
    request: DayAgentDiscoveryBridgeRequest,
    services: DayAgentDiscoveryBridgeServices,
) -> DayAgentDiscoveryBridgeResult:
    binding = services.lineage_binding(request)
    submission = _validated_submission(request, services, binding)
    proposal = _proposal(request, submission, binding, services.researcher_context.sources)
    view = _lineage_view(request, proposal, binding, services)
    if _proposal_semantic_hash(proposal) in view.existing_semantic_hashes:
        return DayAgentDiscoveryBridgeResult(
            accepted=False,
            capsule_id=None,
            first_shadow_eligible_at=view.first_eligible_completed_bar_at,
            terminal_reason="semantic_duplicate",
            cycle_id=hashlib.sha256(
                f"{request.task.task_id}:{request.decision_step.step_id}:semantic_duplicate".encode()
            ).hexdigest(),
            hypothesis_version_id=None,
        )
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
    binding: DayAgentResearchLineageBinding,
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
    try:
        verified_call = services.receipt_store.require_call(request.llm_receipt)
    except ResearcherReceiptStoreError:
        raise DayAgentResearchBridgeError("day_agent_model_receipt_mismatch") from None
    if (
        verified_call.response != step.payload_json.encode()
        or request.llm_receipt.called_at.astimezone(dt.UTC) != step.occurred_at
        or binding.task_id != request.task.task_id
        or binding.step_id != step.step_id
        or binding.call_id != verified_call.record.call_id
        or binding.bound_at != step.occurred_at
    ):
        raise DayAgentResearchBridgeError("day_agent_lineage_binding_invalid")
    if services.evidence_view.market_id is not MarketId.US_EQUITIES:
        raise DayAgentResearchBridgeError("day_agent_market_invalid")
    if request.submitted_at < services.evidence_view.completed_bar_at:
        raise DayAgentResearchBridgeError("day_agent_future_only_boundary_invalid")
    _require_xnys_completed_bar(services.evidence_view.completed_bar_at)
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
    binding: DayAgentResearchLineageBinding,
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
                f"agent_{binding.agent_version}",
                f"champion_{binding.champion_version}",
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
    binding: DayAgentResearchLineageBinding,
    services: DayAgentDiscoveryBridgeServices,
) -> DayDiscoveryEvidenceView:
    view = services.evidence_view
    receipt = request.llm_receipt
    lineage = {
        *view.source_refs,
        *(source.source_id for source in proposal.cited_sources),
        request.task.task_id,
        request.decision_step.step_id,
        receipt.prompt_sha256,
        receipt.response_sha256,
        receipt.model_id,
        binding.binding_id,
        binding.call_id,
        binding.agent_version,
        binding.champion_version,
    }
    observed_at = max(view.observed_at, view.completed_bar_at, request.submitted_at)
    first_eligible = _next_xnys_completed_bar(request.submitted_at)
    semantic_hashes = _ledger_semantic_hashes(services)
    return DayDiscoveryEvidenceView.model_validate(
        view.model_dump(mode="python")
        | {
            "observed_at": observed_at,
            "first_eligible_completed_bar_at": first_eligible,
            "source_refs": tuple(sorted(lineage)),
            "existing_semantic_hashes": semantic_hashes,
        }
    )


def publish_day_agent_research_lineage_binding(
    root: Path,
    *,
    task_id: str,
    step_id: str,
    call_id: str,
    agent_version: str,
    champion_version: str,
    bound_at: dt.datetime,
) -> DayAgentResearchLineageBinding:
    payload = {
        "schema_version": 1,
        "binding_id": "",
        "task_id": task_id,
        "step_id": step_id,
        "call_id": call_id,
        "agent_version": agent_version,
        "champion_version": champion_version,
        "bound_at": bound_at,
    }
    binding = DayAgentResearchLineageBinding.model_validate(
        payload | {"binding_id": _binding_id(payload)}
    )
    try:
        publish_private_immutable_text(
            root / "bindings" / f"{binding.binding_id}.json",
            binding.model_dump_json(),
        )
    except InvalidPrivateImmutableFileError:
        raise DayAgentResearchBridgeError("day_agent_lineage_binding_invalid") from None
    return binding


def _binding_id(payload: dict[str, object]) -> str:
    canonical = dict(payload)
    canonical.pop("binding_id", None)
    return hashlib.sha256(
        json.dumps(
            canonical,
            default=lambda value: (
                value.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
                if isinstance(value, dt.datetime)
                else value
            ),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _require_xnys_completed_bar(completed_at: dt.datetime) -> None:
    local = completed_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(local.date())
    if (
        bounds is None
        or completed_at.second != 0
        or completed_at.microsecond != 0
        or not bounds[0] < local <= bounds[1]
    ):
        raise DayAgentResearchBridgeError("day_agent_completed_bar_not_xnys")


def _next_xnys_completed_bar(submitted_at: dt.datetime) -> dt.datetime:
    local = submitted_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(local.date())
    if bounds is None:
        raise DayAgentResearchBridgeError("day_agent_submission_not_xnys")
    candidate = local.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    if bounds[0] < candidate <= bounds[1]:
        return candidate.astimezone(dt.UTC)
    try:
        next_bounds = regular_session_bounds(next_regular_session(local.date()))
    except UnsupportedUsEquityCalendarDateError:
        raise DayAgentResearchBridgeError("day_agent_next_bar_unavailable") from None
    if next_bounds is None:
        raise DayAgentResearchBridgeError("day_agent_next_bar_unavailable")
    return (next_bounds[0] + dt.timedelta(minutes=1)).astimezone(dt.UTC)


def _ledger_semantic_hashes(
    services: DayAgentDiscoveryBridgeServices,
) -> tuple[str, ...]:
    reader = services.discovery_loop.config.pipeline.stores.ledger.reader()
    families = {stored.family.family_id: stored.family for stored in reader.day_hypothesis_families()}
    hashes = {
        _semantic_hash(
            families[stored.version.family_id].canonical_question,
            families[stored.version.family_id].economic_mechanism,
            stored.version.methodology_tags,
        )
        for stored in reader.day_hypothesis_versions()
        if stored.version.family_id in families
    }
    return tuple(sorted(hashes))


def _semantic_hash(hypothesis: str, mechanism: str, methodology_tags: tuple[str, ...]) -> str:
    return hashlib.sha256(
        "|".join(
            (hypothesis.casefold().strip(), mechanism.casefold().strip(), *methodology_tags)
        ).encode()
    ).hexdigest()


def _proposal_semantic_hash(proposal: ProposedHypothesis) -> str:
    return _semantic_hash(
        proposal.card.hypothesis.hypothesis,
        proposal.card.economic_mechanism,
        proposal.strategy_draft.methodology_tags,
    )


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
    "DayAgentResearchLineageBinding",
    "publish_day_agent_research_lineage_binding",
    "submit_day_agent_hypothesis",
)
