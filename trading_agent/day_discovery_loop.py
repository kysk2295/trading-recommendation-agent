from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.critic_agent import CritiqueReport, Objection, ObjectionKind, Severity
from trading_agent.day_discovery_hypothesis_factory import (
    DayHypothesisBuildInput,
    build_day_hypothesis_contracts,
    day_open_methodology_tags,
)
from trading_agent.day_hypothesis_models import HypothesisFamily, HypothesisVersion
from trading_agent.day_research_attempt_binding import (
    DayResearchAttemptBinding,
    preregistered_attempted_artifact_ref,
)
from trading_agent.day_strategy_capsule import (
    DayStrategyCapsuleRequest,
    GeneratedCapsuleVerification,
    build_strategy_capsule,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
    publish_day_strategy_capsule,
)
from trading_agent.day_strategy_capsule_models import (
    CapsuleArtifactKind,
    CapsuleAuthorityCeiling,
    CapsuleResourceLimits,
    InvalidStrategyCapsuleError,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactError,
    GeneratedStrategyArtifactStore,
)
from trading_agent.generated_strategy_execution import (
    GeneratedStrategyExecutionError,
    GeneratedStrategyLimits,
)
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.heavy_empirical_lease import heavy_empirical_lease
from trading_agent.models import BarInput
from trading_agent.research_agent_actions import ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import (
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    research_agent_result_id,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.researcher_agent import ProposedHypothesis, ResearcherContext
from trading_agent.researcher_pipeline import ResearcherPipeline
from trading_agent.strategy_research_evidence_service import StrategyResearchEvidenceRejected
from trading_agent.strategy_research_results import ResearchAttempt
from trading_agent.strategy_research_types import (
    AttemptStatus,
)


class DayDiscoveryError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


class DayDiscoveryTriggerKind(StrEnum):
    COMPLETED_BAR = "completed_bar"
    POINT_IN_TIME_EVIDENCE = "point_in_time_evidence"
    TERMINAL_EVENT = "terminal_event"
    REVIEW_CLOSE = "review_close"
    EXPLORATION_DUE = "exploration_due"


class DayDiscoveryEvidenceView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: MarketId
    trigger_kind: DayDiscoveryTriggerKind
    observed_at: dt.datetime
    completed_bar_at: dt.datetime
    first_eligible_completed_bar_at: dt.datetime
    universe_snapshot_id: str
    universe_snapshot_at: dt.datetime
    source_refs: tuple[str, ...] = Field(min_length=1)
    evidence_schema: tuple[str, ...] = Field(min_length=1)
    data_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replay_bars: tuple[BarInput, ...] = Field(min_length=1)
    search_budget: int = Field(default=3, ge=1, le=10_000)
    budget_debits_used: int = Field(default=0, ge=0, le=10_000)
    cursor: str = "origin"
    previous_failure: str | None = None
    existing_semantic_hashes: tuple[str, ...] = ()
    resource_limits: GeneratedStrategyLimits = GeneratedStrategyLimits()

    @field_validator("observed_at", "completed_bar_at", "first_eligible_completed_bar_at", "universe_snapshot_at")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return _require_aware_utc(value, "evidence_time_naive")

    @field_validator("source_refs", "evidence_schema", "existing_semantic_hashes")
    @classmethod
    def canonical_sets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def validate_view(self) -> Self:
        if (
            self.universe_snapshot_at > self.completed_bar_at
            or self.completed_bar_at > self.observed_at
            or self.first_eligible_completed_bar_at <= self.completed_bar_at
            or any(bar.timestamp > self.completed_bar_at for bar in self.replay_bars)
            or self.budget_debits_used > self.search_budget
        ):
            raise DayDiscoveryError("evidence_time_invalid")
        return self


class DayDiscoveryFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    family_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    hypothesis_version_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    outcome_class: Literal["supported", "refuted", "inconclusive"] | None = None
    bounded_metrics: dict[str, int | float | str] = Field(default_factory=dict)
    integrity_reason: str | None = None
    data_reason: str | None = None
    runtime_reason: str | None = None
    novelty: Literal["novel", "duplicate", "known"] | None = None
    remaining_budget: int = Field(default=0, ge=0)
    next_review_date: dt.date | None = None
    policy_priority: int | None = Field(default=None, ge=0, le=100)

    @field_validator("integrity_reason", "data_reason", "runtime_reason")
    @classmethod
    def safe_reason(cls, value: str | None) -> str | None:
        if value is not None and (
            not 1 <= len(value) <= 80
            or any(not (character.islower() or character.isdigit() or character == "_") for character in value)
        ):
            raise DayDiscoveryError("feedback_reason_invalid")
        return value

    @field_validator("bounded_metrics")
    @classmethod
    def bounded_preregistered_metrics(
        cls, value: dict[str, int | float | str]
    ) -> dict[str, int | float | str]:
        allowed = {"blocked_count", "coverage_fraction", "signal_count"}
        if set(value) - allowed or any(
            isinstance(item, (str, bool)) or not -1_000_000 <= item <= 1_000_000
            for item in value.values()
        ):
            raise DayDiscoveryError("feedback_metric_not_allowlisted")
        return value


def sanitize_day_discovery_feedback(payload: dict[str, object]) -> DayDiscoveryFeedback:
    allowed = DayDiscoveryFeedback.model_fields.keys()
    return DayDiscoveryFeedback.model_validate({key: value for key, value in payload.items() if key in allowed})


class ForwardProbeAdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    admission_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_id: MarketId
    registration_completed_bar_at: dt.datetime
    first_eligible_completed_bar_at: dt.datetime
    trading_authority: Literal[False] = False

    @classmethod
    def canonical_id_for(cls, payload: dict[str, object]) -> str:
        normalized = {key: value for key, value in payload.items() if key != "admission_id"}
        canonical: dict[str, object] = {}
        for key, value in normalized.items():
            match value:
                case dt.datetime() as timestamp:
                    canonical[key] = _require_aware_utc(
                        timestamp, "forward_probe_time_naive"
                    ).isoformat(
                        timespec="microseconds"
                    ).replace("+00:00", "Z")
                case MarketId() as market:
                    canonical[key] = market.value
                case None | bool() | int() | float() | str():
                    canonical[key] = value
                case _:
                    raise DayDiscoveryError("forward_probe_admission_payload_invalid")
        return _sha(json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True))

    @field_validator("registration_completed_bar_at", "first_eligible_completed_bar_at")
    @classmethod
    def utc_time(cls, value: dt.datetime) -> dt.datetime:
        return _require_aware_utc(value, "forward_probe_time_naive")

    @model_validator(mode="after")
    def future_only(self) -> Self:
        if self.first_eligible_completed_bar_at <= self.registration_completed_bar_at:
            raise DayDiscoveryError("forward_probe_not_future_only")
        if self.admission_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise DayDiscoveryError("forward_probe_admission_id_mismatch")
        return self


class DayDiscoveryCycleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cycle_id: str
    attempt_ids: tuple[str, ...]
    family_id: str | None
    hypothesis_version_id: str | None
    capsule_id: str | None
    admission_id: str | None
    accepted: bool
    terminal_reason: str | None
    drafts_attempted: int
    remaining_budget: int
    first_eligible_completed_bar_at: dt.datetime
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False


@dataclass(frozen=True, slots=True)
class DayDiscoveryLoopConfig:
    pipeline: ResearcherPipeline
    sandbox: GeneratedStrategySandbox
    max_drafts: int = 3


@dataclass(frozen=True, slots=True)
class DayDiscoveryLoop:
    config: DayDiscoveryLoopConfig

    def run(self, view: DayDiscoveryEvidenceView, context: ResearcherContext | None = None) -> DayDiscoveryCycleResult:
        if not 1 <= self.config.max_drafts <= 3:
            raise DayDiscoveryError("max_drafts_out_of_range")
        context = context or _researcher_context()
        cycle_id = _sha(_canonical({
            "market_id": view.market_id.value,
            "trigger": view.trigger_kind.value,
            "observed_at": view.observed_at.isoformat(),
            "cursor": view.cursor,
        }))
        attempt_ids: list[str] = []
        latest_family: HypothesisFamily | None = None
        latest_version: HypothesisVersion | None = None
        terminal_reason: str | None = None
        remaining = view.search_budget - view.budget_debits_used
        if remaining == 0:
            return DayDiscoveryCycleResult(
                cycle_id=cycle_id, attempt_ids=(), family_id=None, hypothesis_version_id=None,
                capsule_id=None, admission_id=None, accepted=False,
                terminal_reason="budget_exhausted", drafts_attempted=0, remaining_budget=0,
                first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
            )
        for branch in range(self.config.max_drafts):
            if remaining == 0:
                break
            current_remaining = remaining
            proposal, critique = self.config.pipeline.propose_candidate(
                context,
                lambda candidate, budget=current_remaining: _day_critique(candidate, view, budget),
            )
            terminal_reason = _critique_terminal_reason(critique)
            family, version, preregistration = build_day_hypothesis_contracts(
                proposal,
                DayHypothesisBuildInput(
                    market_id=view.market_id,
                    observed_at=view.observed_at,
                    completed_bar_at=view.completed_bar_at,
                    first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
                    universe_snapshot_id=view.universe_snapshot_id,
                    universe_snapshot_at=view.universe_snapshot_at,
                    source_refs=view.source_refs,
                    data_manifest_sha256=view.data_manifest_sha256,
                    search_budget=view.search_budget,
                ),
                terminal=terminal_reason is not None,
            )
            latest_family, latest_version = family, version
            attempt_id = _sha(f"{cycle_id}:{branch}:{version.hypothesis_version_id}")
            attempt_ids.append(attempt_id)
            reason = terminal_reason
            published = None
            if reason is None:
                try:
                    published = self.config.pipeline.stores.strategies.publish(proposal)
                except GeneratedStrategyArtifactError:
                    reason = "artifact_publication_failed"
            binding_ref = preregistered_attempted_artifact_ref(version.code_sha256)
            if reason is None and published is not None:
                binding_ref = preregistered_attempted_artifact_ref(published.artifact.payload.source_sha256)
            with self.config.pipeline.stores.ledger.writer() as writer:
                _ = writer.register_strategy_research(preregistration)
                _ = writer.register_day_hypothesis_family(family)
                _ = writer.register_day_hypothesis_version(version)
            if reason is not None or published is None:
                _record_terminal(
                    self.config.pipeline.stores.ledger,
                    attempt_id, branch, version, binding_ref, view, reason or "failed",
                )
                remaining -= 1
                terminal_reason = reason
                continue
            binding_payload = {
                "binding_id": "", "attempt_id": attempt_id, "market_id": version.market_id,
                "hypothesis_version_id": version.hypothesis_version_id,
                "artifact_ref": binding_ref, "multiple_testing_family": version.multiple_testing_family,
                "search_budget_debit": 1, "bound_at": view.observed_at + dt.timedelta(seconds=2),
            }
            prospective_binding = DayResearchAttemptBinding.model_validate(
                binding_payload | {"binding_id": DayResearchAttemptBinding.canonical_id_for(binding_payload)}
            )
            request = _capsule_request(
                version, prospective_binding, published.artifact.artifact_id, view,
                self.config.pipeline.stores.strategies, self.config.sandbox,
            )
            try:
                _ = build_strategy_capsule(request)
            except (GeneratedStrategyExecutionError, InvalidStrategyCapsuleError) as error:
                reason = _preflight_reason(error)
                _record_terminal(
                    self.config.pipeline.stores.ledger, attempt_id, branch, version, binding_ref, view, reason
                )
                remaining -= 1
                terminal_reason = reason
                continue
            successful_attempt = ResearchAttempt(
                attempt_id=attempt_id,
                hypothesis_id=preregistration.hypothesis.hypothesis_id,
                branch_index=branch,
                input_hashes=(view.data_manifest_sha256,),
                code_sha256=version.code_sha256,
                data_manifest_sha256=view.data_manifest_sha256,
                started_at=view.observed_at,
                finished_at=view.observed_at + dt.timedelta(seconds=1),
                status=AttemptStatus.SUCCEEDED,
                artifact_refs=(binding_ref,),
                error_class=None,
                max_cpu_seconds=view.resource_limits.cpu_seconds,
            )
            binding = _binding(
                successful_attempt, version, binding_ref, view.observed_at + dt.timedelta(seconds=2)
            )
            with self.config.pipeline.stores.ledger.writer() as writer:
                _ = writer.append_strategy_research_attempt(successful_attempt)
                _ = writer.register_day_research_attempt_binding(binding)
            capsule, _ = publish_day_strategy_capsule(self.config.pipeline.stores.ledger, request)
            admission_payload = {
                "admission_id": "",
                "capsule_id": capsule.capsule_id,
                "market_id": view.market_id,
                "registration_completed_bar_at": view.completed_bar_at,
                "first_eligible_completed_bar_at": view.first_eligible_completed_bar_at,
                "trading_authority": False,
            }
            admission = ForwardProbeAdmissionRequest(
                admission_id=ForwardProbeAdmissionRequest.canonical_id_for(admission_payload),
                capsule_id=capsule.capsule_id,
                market_id=view.market_id,
                registration_completed_bar_at=view.completed_bar_at,
                first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
            )
            return DayDiscoveryCycleResult(
                cycle_id=cycle_id,
                attempt_ids=tuple(attempt_ids),
                family_id=family.family_id,
                hypothesis_version_id=version.hypothesis_version_id,
                capsule_id=capsule.capsule_id,
                admission_id=admission.admission_id,
                accepted=True,
                terminal_reason=None,
                drafts_attempted=len(attempt_ids),
                remaining_budget=remaining - 1,
                first_eligible_completed_bar_at=admission.first_eligible_completed_bar_at,
            )
        return DayDiscoveryCycleResult(
            cycle_id=cycle_id,
            attempt_ids=tuple(attempt_ids),
            family_id=None if latest_family is None else latest_family.family_id,
            hypothesis_version_id=None if latest_version is None else latest_version.hypothesis_version_id,
            capsule_id=None,
            admission_id=None,
            accepted=False,
            terminal_reason=terminal_reason,
            drafts_attempted=len(attempt_ids),
            remaining_budget=remaining,
            first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
        )


@dataclass(frozen=True, slots=True)
class DayDiscoveryActionExecutor:
    loop: DayDiscoveryLoop
    researcher_context: ResearcherContext

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        if context.cycle.agent_family_id != "day_trading":
            raise DayDiscoveryError("action_family_identity_mismatch")
        selected = tuple(
            item for item in context.evidence
            if item.bounded_payload_json is not None
            and set(context.decision.subject_refs).intersection((str(item.evidence_id), *item.subject_refs))
        )
        if len(selected) != 1:
            raise DayDiscoveryError("bounded_discovery_evidence_unresolved")
        payload = selected[0].bounded_payload_json
        if payload is None:
            raise DayDiscoveryError("bounded_discovery_evidence_unresolved")
        view = DayDiscoveryEvidenceView.model_validate_json(payload)
        if view.market_id.value != context.cycle.market_id:
            raise DayDiscoveryError("discovery_market_identity_mismatch")
        with heavy_empirical_lease(self.loop.config.pipeline.stores.ledger.path):
            result = self.loop.run(view, self.researcher_context)
        artifacts = tuple(
            value for value in (
                result.family_id, result.hypothesis_version_id, result.capsule_id, result.admission_id
            ) if value is not None
        )
        no_artifact_terminal = not result.accepted and not artifacts
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(context.cycle.cycle_id),
            cycle_id=context.cycle.cycle_id, agent_family_id="day_trading",
            market_id=context.cycle.market_id,
            status=(
                ResearchAgentResultStatus.NO_ACTION
                if no_artifact_terminal else ResearchAgentResultStatus.COMPLETED
            ),
            question=context.decision.question,
            summary=(
                f"Day Discovery accepted one future-only capsule ({result.capsule_id})."
                if result.accepted else
                f"Day Discovery ended terminally after bounded criticism ({result.terminal_reason})."
            ),
            reason=result.terminal_reason, evidence_refs=context.decision.evidence_refs,
            continuation=(
                "Wait for a new market-local evidence trigger or refreshed exploration budget."
                if no_artifact_terminal else None
            ),
            artifact_refs=artifacts, occurred_at=context.observed_at,
            next_wake_kind=context.decision.next_wake_kind,
            next_wake_at=context.decision.next_wake_at,
        )


def _critic_reason(proposal: ProposedHypothesis, view: DayDiscoveryEvidenceView, remaining: int) -> str | None:
    if remaining < 1:
        return "budget_exhausted"
    if len(proposal.strategy_draft.free_parameters) > remaining:
        return "budget_exhausted"
    if _proposal_semantic_hash(proposal) in view.existing_semantic_hashes:
        return "semantic_duplicate"
    try:
        _ = day_open_methodology_tags(proposal)
    except StrategyResearchEvidenceRejected:
        return "methodology_missing"
    source = proposal.strategy_draft.source_code
    lowered = source.casefold()
    if any(token in lowered for token in ("future_bar", "lookahead", "revised_data", "target_leak")):
        return "point_in_time_leakage"
    if not proposal.card.counterfactual_baseline.strip() or not proposal.card.hypothesis.falsification_rule.strip():
        return "critic_rejected"
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "compile_failed"
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "create_strategy" not in names or "observe" not in names:
        return "unconstructible"
    return None


def _day_critique(
    proposal: ProposedHypothesis, view: DayDiscoveryEvidenceView, remaining: int
) -> CritiqueReport:
    reason = _critic_reason(proposal, view, remaining)
    return CritiqueReport(
        () if reason is None else (
            Objection(ObjectionKind.SOURCE_FIDELITY, Severity.BLOCKING, reason),
        )
    )


def _critique_terminal_reason(critique: CritiqueReport) -> str | None:
    blocking = tuple(item for item in critique.objections if item.severity is Severity.BLOCKING)
    if not blocking:
        return None
    known = {
        "artifact_publication_failed", "budget_exhausted", "compile_failed", "critic_rejected",
        "methodology_missing", "point_in_time_leakage", "semantic_duplicate", "unconstructible",
    }
    return blocking[0].evidence if blocking[0].evidence in known else "critic_rejected"


def _record_terminal(
    ledger: ExperimentLedgerStore, attempt_id: str, branch: int, version: HypothesisVersion,
    artifact_ref: str, view: DayDiscoveryEvidenceView, reason: str,
) -> None:
    attempt = ResearchAttempt(
        attempt_id=attempt_id, hypothesis_id=version.hypothesis_version_id, branch_index=branch,
        input_hashes=(view.data_manifest_sha256,), code_sha256=version.code_sha256,
        data_manifest_sha256=view.data_manifest_sha256, started_at=view.observed_at,
        finished_at=view.observed_at + dt.timedelta(seconds=1), status=AttemptStatus.FAILED, artifact_refs=(),
        error_class=reason, max_cpu_seconds=view.resource_limits.cpu_seconds,
    )
    binding = _binding(attempt, version, artifact_ref, view.observed_at + dt.timedelta(seconds=2))
    with ledger.writer() as writer:
        _ = writer.append_strategy_research_attempt(attempt)
        _ = writer.register_day_research_attempt_binding(binding)


def _binding(
    attempt: ResearchAttempt, version: HypothesisVersion, artifact_ref: str, bound_at: dt.datetime
) -> DayResearchAttemptBinding:
    payload = {
        "binding_id": "", "attempt_id": attempt.attempt_id, "market_id": version.market_id,
        "hypothesis_version_id": version.hypothesis_version_id, "artifact_ref": artifact_ref,
        "multiple_testing_family": version.multiple_testing_family, "search_budget_debit": 1,
        "bound_at": bound_at,
    }
    return DayResearchAttemptBinding.model_validate(
        payload | {"binding_id": DayResearchAttemptBinding.canonical_id_for(payload)}
    )


def _capsule_request(
    version: HypothesisVersion, binding: DayResearchAttemptBinding, artifact_id: str,
    view: DayDiscoveryEvidenceView, artifact_store: GeneratedStrategyArtifactStore,
    sandbox: GeneratedStrategySandbox,
) -> DayStrategyCapsuleRequest:
    limits = CapsuleResourceLimits(**asdict(view.resource_limits))
    return DayStrategyCapsuleRequest(
        hypothesis_version_id=version.hypothesis_version_id, attempt_binding_id=binding.binding_id,
        market_id=view.market_id, artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
        artifact_ref=preregistered_attempted_artifact_ref(version.code_sha256),
        artifact_sha256=version.code_sha256, generated_artifact_id=artifact_id,
        evaluation_cadence=version.evaluation_cadence, evidence_schema=view.evidence_schema,
        entry_rule=version.entry_rule, exit_rule=version.exit_rule, stop_rule=version.stop_rule,
        target_rule="host_projects_preregistered_targets", cost_model=version.cost_model,
        slippage_model_id="bounded_intraday_slippage_v1", resource_limits=limits,
        risk_policy_ref="risk-policy://day-research/v1", protocol_version=1,
        protocol_sha256=generated_protocol_bundle_sha256(), evaluator_sha256=generated_evaluator_bundle_sha256(),
        published_at=view.observed_at + dt.timedelta(seconds=3),
        authority_ceiling=CapsuleAuthorityCeiling.RESEARCH_ONLY,
        generated_verification=GeneratedCapsuleVerification(artifact_store, sandbox, view.replay_bars),
    )


def _preflight_reason(error: GeneratedStrategyExecutionError | InvalidStrategyCapsuleError) -> str:
    reason = error.reason
    if "nondeterministic" in reason:
        return "nondeterministic"
    return "sandbox_failed"


def _researcher_context() -> ResearcherContext:
    from trading_agent.lane_identity_models import LaneId
    from trading_agent.researcher_agent import FailureDigest

    return ResearcherContext(LaneId.INTRADAY_MOMENTUM, (), FailureDigest((), (), ()), "bounded_day_view", ())


def _canonical(payload: dict[str, str]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _require_aware_utc(value: dt.datetime, reason: str) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DayDiscoveryError(reason)
    return value.astimezone(dt.UTC)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _proposal_semantic_hash(proposal: ProposedHypothesis) -> str:
    payload = "|".join(
        (
            proposal.card.hypothesis.hypothesis.casefold().strip(),
            proposal.card.economic_mechanism.casefold().strip(),
            *proposal.strategy_draft.methodology_tags,
        )
    )
    return _sha(payload)


__all__ = (
    "DayDiscoveryActionExecutor",
    "DayDiscoveryCycleResult",
    "DayDiscoveryError",
    "DayDiscoveryEvidenceView",
    "DayDiscoveryFeedback",
    "DayDiscoveryLoop",
    "DayDiscoveryLoopConfig",
    "DayDiscoveryTriggerKind",
    "ForwardProbeAdmissionRequest",
    "sanitize_day_discovery_feedback",
)
