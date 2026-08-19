from __future__ import annotations

import ast
import datetime as dt
import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from trading_agent.critic_agent import CritiqueReport, Objection, ObjectionKind, Severity
from trading_agent.day_discovery_hypothesis_factory import (
    DayHypothesisBuildInput,
    build_day_hypothesis_contracts,
    day_open_methodology_tags,
)
from trading_agent.day_discovery_journal import (
    TERMINAL_REASONS,
    DayDiscoveryBranchReservation,
    DayDiscoveryBranchResolution,
    DayDiscoveryPreparedBranch,
    InvalidDayDiscoveryJournalError,
    PreparedLlmReceipt,
    PreparedStrategyDraft,
    prepared_branch_path,
    publish_prepared_branch,
    publish_reservation,
    publish_resolution,
    read_prepared_branch,
    read_reservation,
    read_resolution,
    reservation_path,
    resolution_path,
)
from trading_agent.day_hypothesis_models import HypothesisFamily, HypothesisVersion
from trading_agent.day_research_attempt_binding import (
    DayResearchAttemptBinding,
    preregistered_attempted_artifact_ref,
)
from trading_agent.day_sensitive_content import contains_sensitive_text
from trading_agent.day_strategy_capsule import (
    DayStrategyCapsuleRequest,
    GeneratedCapsuleVerification,
    _publish_prebuilt_day_strategy_capsule,
    build_strategy_capsule,
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
)
from trading_agent.day_strategy_capsule_models import (
    CapsuleArtifactKind,
    CapsuleAuthorityCeiling,
    CapsuleResourceLimits,
    InvalidStrategyCapsuleError,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
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
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
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
        return _safe_reason_token(value)

    @field_validator("bounded_metrics")
    @classmethod
    def bounded_preregistered_metrics(cls, value: dict[str, int | float | str]) -> dict[str, int | float | str]:
        allowed = {"blocked_count", "coverage_fraction", "signal_count"}
        if set(value) - allowed or any(
            isinstance(item, (str, bool)) or not -1_000_000 <= item <= 1_000_000 for item in value.values()
        ):
            raise DayDiscoveryError("feedback_metric_not_allowlisted")
        return value


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
    budget_epoch_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    search_budget: int = Field(default=3, ge=1, le=10_000)
    budget_debits_used: int = Field(default=0, ge=0, le=10_000)
    cursor: str = "origin"
    previous_failure: str | None = None
    existing_semantic_hashes: tuple[str, ...] = ()
    feedback: DayDiscoveryFeedback | None = None
    resource_limits: GeneratedStrategyLimits = GeneratedStrategyLimits()

    @field_validator("observed_at", "completed_bar_at", "first_eligible_completed_bar_at", "universe_snapshot_at")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        return _require_aware_utc(value, "evidence_time_naive")

    @field_validator("source_refs", "evidence_schema", "existing_semantic_hashes")
    @classmethod
    def canonical_sets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(values)))

    @field_validator("previous_failure")
    @classmethod
    def safe_previous_failure(cls, value: str | None) -> str | None:
        return _safe_reason_token(value)

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


class DayDiscoveryPromptBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    prior_close: float
    average_daily_volume: int
    spread_bps: float


class DayDiscoveryPromptLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wall_seconds: float
    cpu_seconds: int
    rss_bytes: int
    open_files: int
    output_bytes: int


class DayDiscoveryPromptView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: MarketId
    trigger_kind: DayDiscoveryTriggerKind
    observed_at: dt.datetime
    completed_bar_at: dt.datetime
    first_eligible_completed_bar_at: dt.datetime
    universe_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    universe_snapshot_at: dt.datetime
    source_refs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ref_count: int = Field(ge=1)
    evidence_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_schema_count: int = Field(ge=1)
    data_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replay_bars: tuple[DayDiscoveryPromptBar, ...] = Field(min_length=1)
    search_budget: int = Field(ge=1)
    budget_debits_used: int = Field(ge=0)
    remaining_budget: int = Field(ge=0)
    cursor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_failure: str | None
    existing_semantic_hashes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    existing_semantic_hash_count: int = Field(ge=0)
    feedback: DayDiscoveryFeedback | None
    resource_limits: DayDiscoveryPromptLimits


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
                    canonical[key] = (
                        _require_aware_utc(timestamp, "forward_probe_time_naive")
                        .isoformat(timespec="microseconds")
                        .replace("+00:00", "Z")
                    )
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

    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_ids: tuple[str, ...]
    family_id: str | None
    hypothesis_version_id: str | None
    capsule_id: str | None
    admission_id: str | None
    accepted: bool
    terminal_reason: str | None
    drafts_attempted: int = Field(ge=0, le=3)
    remaining_budget: int = Field(ge=0)
    first_eligible_completed_bar_at: dt.datetime
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        identifiers = (self.family_id, self.hypothesis_version_id, self.capsule_id, self.admission_id)
        if len(self.attempt_ids) != self.drafts_attempted or len(set(self.attempt_ids)) != len(self.attempt_ids):
            raise DayDiscoveryError("cycle_result_attempts_invalid")
        if self.accepted:
            if any(value is None for value in identifiers) or self.terminal_reason is not None:
                raise DayDiscoveryError("cycle_result_acceptance_invalid")
        elif (
            self.capsule_id is not None
            or self.admission_id is not None
            or self.terminal_reason not in TERMINAL_REASONS
        ):
            raise DayDiscoveryError("cycle_result_terminal_invalid")
        return self


class DayDiscoveryCycleReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: DayDiscoveryCycleResult

    @model_validator(mode="after")
    def matching_cycle_identity(self) -> Self:
        if self.cycle_id != self.result.cycle_id:
            raise DayDiscoveryError("cycle_receipt_result_identity_conflict")
        return self


@dataclass(frozen=True, slots=True)
class DayDiscoveryLoopConfig:
    pipeline: ResearcherPipeline
    sandbox: GeneratedStrategySandbox
    max_drafts: int = 3
    cycle_receipt_root: Path | None = None
    clock: Callable[[], dt.datetime] | None = None
    fault_injector: Callable[[str], None] | None = None


@dataclass(frozen=True, slots=True)
class DayDiscoveryLoop:
    config: DayDiscoveryLoopConfig

    def run(self, view: DayDiscoveryEvidenceView, context: ResearcherContext | None = None) -> DayDiscoveryCycleResult:
        if not 1 <= self.config.max_drafts <= 3:
            raise DayDiscoveryError("max_drafts_out_of_range")
        cycle_id = _sha(
            _canonical(
                {
                    "market_id": view.market_id.value,
                    "trigger": view.trigger_kind.value,
                    "observed_at": view.observed_at.isoformat(),
                    "cursor": view.cursor,
                }
            )
        )
        evidence_sha256 = _sha(_canonical_view(view))
        receipt_root = self.config.cycle_receipt_root or (
            self.config.pipeline.artifacts.manifest_root / "day-discovery-cycle-receipts"
        )
        receipt_path = receipt_root / f"{cycle_id}.json"
        with _cycle_receipt_lease(receipt_root, cycle_id):
            replay = _read_cycle_receipt(receipt_path, cycle_id, evidence_sha256)
            if replay is not None:
                return replay
            result = self._run_new_cycle(
                view,
                context or _researcher_context(),
                cycle_id,
                evidence_sha256,
                receipt_root,
            )
            receipt = DayDiscoveryCycleReceipt(
                cycle_id=cycle_id,
                evidence_sha256=evidence_sha256,
                result=result,
            )
            try:
                _ = publish_private_immutable_text(
                    receipt_path,
                    json.dumps(
                        receipt.model_dump(mode="json"),
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            except InvalidPrivateImmutableFileError:
                raise DayDiscoveryError("cycle_receipt_publication_failed") from None
            return result

    def _run_new_cycle(
        self,
        view: DayDiscoveryEvidenceView,
        context: ResearcherContext,
        cycle_id: str,
        evidence_sha256: str,
        receipt_root: Path,
    ) -> DayDiscoveryCycleResult:
        attempt_ids: list[str] = []
        latest_family: HypothesisFamily | None = None
        latest_version: HypothesisVersion | None = None
        terminal_reason: str | None = None
        remaining = view.search_budget - view.budget_debits_used
        _require_safe_day_context(context)
        context = replace(
            context,
            bounded_day_discovery_json=_bounded_prompt_view(view, remaining),
        )
        if remaining == 0:
            return DayDiscoveryCycleResult(
                cycle_id=cycle_id,
                attempt_ids=(),
                family_id=None,
                hypothesis_version_id=None,
                capsule_id=None,
                admission_id=None,
                accepted=False,
                terminal_reason="budget_exhausted",
                drafts_attempted=0,
                remaining_budget=0,
                first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
            )
        for branch in range(self.config.max_drafts):
            if remaining == 0:
                break
            prepared = self._prepared_branch(
                view,
                context,
                cycle_id,
                evidence_sha256,
                receipt_root,
                branch,
                remaining,
            )
            if prepared is None:
                attempt_ids.append(_sha(f"{cycle_id}:{branch}:model_call_interrupted"))
                remaining -= 1
                terminal_reason = "model_call_interrupted"
                break
            latest_family, latest_version = prepared.family, prepared.version
            attempt_ids.append(prepared.attempt_id)
            capsule_id, admission_id, reason = self._execute_prepared_branch(prepared, view, receipt_root)
            remaining -= prepared.search_budget_debit
            if capsule_id is not None and admission_id is not None:
                return DayDiscoveryCycleResult(
                    cycle_id=cycle_id,
                    attempt_ids=tuple(attempt_ids),
                    family_id=prepared.family.family_id,
                    hypothesis_version_id=prepared.version.hypothesis_version_id,
                    capsule_id=capsule_id,
                    admission_id=admission_id,
                    accepted=True,
                    terminal_reason=None,
                    drafts_attempted=len(attempt_ids),
                    remaining_budget=remaining,
                    first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
                )
            terminal_reason = reason
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

    def _prepared_branch(
        self,
        view: DayDiscoveryEvidenceView,
        context: ResearcherContext,
        cycle_id: str,
        evidence_sha256: str,
        receipt_root: Path,
        branch: int,
        remaining: int,
    ) -> DayDiscoveryPreparedBranch | None:
        path = prepared_branch_path(receipt_root, cycle_id, branch)
        try:
            stored = read_prepared_branch(path, cycle_id, evidence_sha256, branch)
            reservation = read_reservation(reservation_path(receipt_root, cycle_id, branch))
        except InvalidDayDiscoveryJournalError as error:
            raise DayDiscoveryError(error.reason) from None
        if stored is not None:
            if reservation is None:
                raise DayDiscoveryError("prepared_branch_reservation_missing")
            _validate_reservation(reservation, view, cycle_id, evidence_sha256, branch, remaining)
            _validate_prepared(stored, view, cycle_id, evidence_sha256, branch, remaining)
            return stored
        if reservation is not None:
            _validate_reservation(reservation, view, cycle_id, evidence_sha256, branch, remaining)
            return None
        reservation = DayDiscoveryBranchReservation(
            cycle_id=cycle_id,
            evidence_sha256=evidence_sha256,
            branch_index=branch,
            market_id=view.market_id.value,
            search_budget=view.search_budget,
            remaining_budget_before=remaining,
            reserved_at=_cycle_time(self.config.clock, view.observed_at),
        )
        try:
            publish_reservation(reservation_path(receipt_root, cycle_id, branch), reservation)
        except InvalidDayDiscoveryJournalError as error:
            raise DayDiscoveryError(error.reason) from None
        proposal, critique = self.config.pipeline.propose_candidate(
            context,
            lambda candidate: _day_critique(candidate, view, remaining),
        )
        reason = _critique_terminal_reason(critique)
        attempt_started_at = proposal.llm_receipt.called_at.astimezone(dt.UTC)
        actual_registration_at = max(attempt_started_at, view.completed_bar_at, view.observed_at)
        if attempt_started_at < view.observed_at:
            reason = "proposal_time_invalid"
        if actual_registration_at >= view.first_eligible_completed_bar_at:
            reason = "forward_probe_not_future_only"
        contract_first_eligible_at = max(
            view.first_eligible_completed_bar_at,
            actual_registration_at + dt.timedelta(microseconds=1),
        )
        attempt_finished_at = _cycle_time(self.config.clock, actual_registration_at)
        bound_at = _cycle_time(self.config.clock, attempt_finished_at)
        published_at = _cycle_time(self.config.clock, bound_at)
        family, version, preregistration = build_day_hypothesis_contracts(
            proposal,
            DayHypothesisBuildInput(
                market_id=view.market_id,
                observed_at=actual_registration_at,
                completed_bar_at=view.completed_bar_at,
                first_eligible_completed_bar_at=contract_first_eligible_at,
                universe_snapshot_id=view.universe_snapshot_id,
                universe_snapshot_at=view.universe_snapshot_at,
                source_refs=view.source_refs,
                data_manifest_sha256=view.data_manifest_sha256,
                search_budget=remaining,
            ),
            terminal=reason is not None,
        )
        attempt_id = _sha(f"{cycle_id}:{branch}:{version.hypothesis_version_id}")
        debit = _search_budget_debit(proposal, remaining)
        prepared = DayDiscoveryPreparedBranch(
            cycle_id=cycle_id,
            evidence_sha256=evidence_sha256,
            branch_index=branch,
            market_id=view.market_id.value,
            search_budget=view.search_budget,
            remaining_budget_before=remaining,
            proposal_card=proposal.card.model_dump(mode="json"),
            cited_sources=proposal.cited_sources,
            llm_receipt=PreparedLlmReceipt(**asdict(proposal.llm_receipt)),
            strategy_draft=PreparedStrategyDraft(**asdict(proposal.strategy_draft)),
            terminal_reason=reason,
            family=family,
            version=version,
            preregistration=preregistration,
            attempt_id=attempt_id,
            attempt_started_at=attempt_started_at,
            attempt_finished_at=attempt_finished_at,
            bound_at=bound_at,
            published_at=published_at,
            search_budget_debit=debit,
        )
        try:
            publish_prepared_branch(path, prepared)
        except InvalidDayDiscoveryJournalError as error:
            raise DayDiscoveryError(error.reason) from None
        _validate_prepared(prepared, view, cycle_id, evidence_sha256, branch, remaining)
        return prepared

    def _execute_prepared_branch(
        self,
        prepared: DayDiscoveryPreparedBranch,
        view: DayDiscoveryEvidenceView,
        receipt_root: Path,
    ) -> tuple[str | None, str | None, str | None]:
        path = resolution_path(receipt_root, prepared.cycle_id, prepared.branch_index)
        try:
            resolution = read_resolution(path)
        except InvalidDayDiscoveryJournalError as error:
            raise DayDiscoveryError(error.reason) from None
        if resolution is not None and (
            resolution.cycle_id != prepared.cycle_id
            or resolution.evidence_sha256 != prepared.evidence_sha256
            or resolution.branch_index != prepared.branch_index
            or resolution.attempt_id != prepared.attempt_id
        ):
            raise DayDiscoveryError("resolution_identity_conflict")
        if resolution is None:
            resolution = self._resolve_prepared_branch(prepared, view)
            try:
                publish_resolution(path, resolution)
            except InvalidDayDiscoveryJournalError as error:
                raise DayDiscoveryError(error.reason) from None
        binding_ref = resolution.artifact_ref or preregistered_attempted_artifact_ref(
            prepared.version.code_sha256
        )
        with self.config.pipeline.stores.ledger.writer() as writer:
            _ = writer.register_strategy_research(prepared.preregistration)
            _ = writer.register_day_hypothesis_family(prepared.family)
            _ = writer.register_day_hypothesis_version(prepared.version)
        if self.config.fault_injector is not None:
            self.config.fault_injector("version_registered")
        if resolution.outcome == "terminal":
            _record_terminal(
                self.config.pipeline.stores.ledger,
                prepared.attempt_id,
                prepared.branch_index,
                prepared.version,
                binding_ref,
                view,
                resolution.terminal_reason or "critic_rejected",
                prepared.attempt_started_at,
                prepared.attempt_finished_at,
                prepared.bound_at,
                prepared.search_budget_debit,
                view.search_budget,
            )
            return None, None, resolution.terminal_reason
        successful_attempt = ResearchAttempt(
            attempt_id=prepared.attempt_id,
            hypothesis_id=prepared.preregistration.hypothesis.hypothesis_id,
            branch_index=prepared.branch_index,
            input_hashes=(view.data_manifest_sha256,),
            code_sha256=prepared.version.code_sha256,
            data_manifest_sha256=view.data_manifest_sha256,
            started_at=prepared.attempt_started_at,
            finished_at=prepared.attempt_finished_at,
            status=AttemptStatus.SUCCEEDED,
            artifact_refs=(binding_ref,),
            error_class=None,
            max_cpu_seconds=view.resource_limits.cpu_seconds,
        )
        binding = _binding(
            successful_attempt,
            prepared.version,
            binding_ref,
            prepared.bound_at,
            prepared.search_budget_debit,
            view.search_budget,
        )
        with self.config.pipeline.stores.ledger.writer() as writer:
            _ = writer.append_strategy_research_attempt(successful_attempt)
            _ = writer.register_day_research_attempt_binding(binding)
        capsule = resolution.capsule
        if capsule is None:
            raise DayDiscoveryError("resolution_capsule_missing")
        _ = _publish_prebuilt_day_strategy_capsule(self.config.pipeline.stores.ledger, capsule)
        admission_payload = {
            "admission_id": "",
            "capsule_id": capsule.capsule_id,
            "market_id": view.market_id,
            "registration_completed_bar_at": prepared.published_at,
            "first_eligible_completed_bar_at": view.first_eligible_completed_bar_at,
            "trading_authority": False,
        }
        admission = ForwardProbeAdmissionRequest(
            admission_id=ForwardProbeAdmissionRequest.canonical_id_for(admission_payload),
            capsule_id=capsule.capsule_id,
            market_id=view.market_id,
            registration_completed_bar_at=prepared.published_at,
            first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
        )
        return capsule.capsule_id, admission.admission_id, None

    def _resolve_prepared_branch(
        self,
        prepared: DayDiscoveryPreparedBranch,
        view: DayDiscoveryEvidenceView,
    ) -> DayDiscoveryBranchResolution:
        reason = prepared.terminal_reason
        published = None
        if reason is None:
            try:
                published = self.config.pipeline.stores.strategies.publish(prepared.proposal())
            except GeneratedStrategyArtifactError:
                reason = "artifact_publication_failed"
        artifact_ref = preregistered_attempted_artifact_ref(prepared.version.code_sha256)
        artifact_id = None
        capsule = None
        if published is not None:
            artifact_id = published.artifact.artifact_id
            artifact_ref = preregistered_attempted_artifact_ref(published.artifact.payload.source_sha256)
        if reason is None and artifact_id is not None:
            binding = _binding_for(
                prepared.attempt_id,
                prepared.version,
                artifact_ref,
                prepared.bound_at,
                prepared.search_budget_debit,
                view.search_budget,
            )
            request = _capsule_request(
                prepared.version,
                binding,
                artifact_id,
                view,
                self.config.pipeline.stores.strategies,
                self.config.sandbox,
                prepared.published_at,
            )
            if prepared.version.first_shadow_eligible_at <= prepared.published_at:
                reason = "forward_probe_not_future_only"
            else:
                try:
                    capsule = build_strategy_capsule(request)
                except (GeneratedStrategyExecutionError, InvalidStrategyCapsuleError) as error:
                    reason = _preflight_reason(error)
        return DayDiscoveryBranchResolution(
            cycle_id=prepared.cycle_id,
            evidence_sha256=prepared.evidence_sha256,
            branch_index=prepared.branch_index,
            attempt_id=prepared.attempt_id,
            outcome="success" if reason is None else "terminal",
            terminal_reason=reason,
            artifact_id=artifact_id,
            artifact_ref=artifact_ref,
            capsule=capsule,
        )


@dataclass(frozen=True, slots=True)
class DayDiscoveryActionExecutor:
    loop: DayDiscoveryLoop
    researcher_context: ResearcherContext

    def execute(self, context: ResearchAgentActionContext) -> ResearchAgentResultV1:
        if context.cycle.agent_family_id != "day_trading":
            raise DayDiscoveryError("action_family_identity_mismatch")
        selected = tuple(
            item
            for item in context.evidence
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
        try:
            with heavy_empirical_lease(self.loop.config.pipeline.stores.ledger.path):
                result = self.loop.run(view, self.researcher_context)
        except DayDiscoveryError:
            raise
        except (
            ExperimentLedgerConflictError,
            GeneratedStrategyArtifactError,
            InvalidDayDiscoveryJournalError,
            InvalidExperimentLedgerSourceError,
            InvalidStrategyCapsuleError,
            StrategyResearchEvidenceRejected,
            ValidationError,
        ):
            raise DayDiscoveryError("day_discovery_persistence_failed") from None
        artifacts = tuple(
            value
            for value in (result.family_id, result.hypothesis_version_id, result.capsule_id, result.admission_id)
            if value is not None
        )
        no_artifact_terminal = not result.accepted and not artifacts
        return ResearchAgentResultV1(
            result_id=research_agent_result_id(context.cycle.cycle_id),
            cycle_id=context.cycle.cycle_id,
            agent_family_id="day_trading",
            market_id=context.cycle.market_id,
            status=(
                ResearchAgentResultStatus.NO_ACTION if no_artifact_terminal else ResearchAgentResultStatus.COMPLETED
            ),
            question=context.decision.question,
            summary=(
                f"Day Discovery accepted one future-only capsule ({result.capsule_id})."
                if result.accepted
                else f"Day Discovery ended terminally after bounded criticism ({result.terminal_reason})."
            ),
            reason=result.terminal_reason,
            evidence_refs=context.decision.evidence_refs,
            continuation=(
                "Wait for a new market-local evidence trigger or refreshed exploration budget."
                if no_artifact_terminal
                else None
            ),
            artifact_refs=artifacts,
            occurred_at=context.observed_at,
            next_wake_kind=context.decision.next_wake_kind,
            next_wake_at=context.decision.next_wake_at,
        )


def _critic_reason(proposal: ProposedHypothesis, view: DayDiscoveryEvidenceView, remaining: int) -> str | None:
    if remaining < 1:
        return "budget_exhausted"
    if any(
        not _canonical_ai_text(value)
        for value in (
            proposal.card.hypothesis.hypothesis,
            proposal.card.economic_mechanism,
            proposal.card.counterfactual_baseline,
        )
    ) or (
        bool(proposal.card.hypothesis.falsification_rule)
        and not _canonical_ai_text(proposal.card.hypothesis.falsification_rule)
    ):
        return "contract_invalid"
    if (
        any(
            not _canonical_ai_text(value) or len(value) > 80
            for value in proposal.strategy_draft.free_parameters
        )
        or len(set(proposal.strategy_draft.free_parameters)) > 12
    ):
        return "contract_invalid"
    if _parameter_combination_demand(proposal) > remaining:
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


def _canonical_ai_text(value: str) -> bool:
    return bool(value) and value == value.strip() and value.isprintable()


def _validate_reservation(
    reservation: DayDiscoveryBranchReservation,
    view: DayDiscoveryEvidenceView,
    cycle_id: str,
    evidence_sha256: str,
    branch: int,
    remaining: int,
) -> None:
    if (
        reservation.cycle_id != cycle_id
        or reservation.evidence_sha256 != evidence_sha256
        or reservation.branch_index != branch
        or reservation.market_id != view.market_id.value
        or reservation.search_budget != view.search_budget
        or reservation.remaining_budget_before != remaining
    ):
        raise DayDiscoveryError("reservation_identity_conflict")


def _validate_prepared(
    prepared: DayDiscoveryPreparedBranch,
    view: DayDiscoveryEvidenceView,
    cycle_id: str,
    evidence_sha256: str,
    branch: int,
    remaining: int,
) -> None:
    proposal = prepared.proposal()
    expected_registration = max(prepared.attempt_started_at, view.completed_bar_at, view.observed_at)
    expected_eligibility = max(
        view.first_eligible_completed_bar_at,
        expected_registration + dt.timedelta(microseconds=1),
    )
    expected = build_day_hypothesis_contracts(
        proposal,
        DayHypothesisBuildInput(
            market_id=view.market_id,
            observed_at=expected_registration,
            completed_bar_at=view.completed_bar_at,
            first_eligible_completed_bar_at=expected_eligibility,
            universe_snapshot_id=view.universe_snapshot_id,
            universe_snapshot_at=view.universe_snapshot_at,
            source_refs=view.source_refs,
            data_manifest_sha256=view.data_manifest_sha256,
            search_budget=remaining,
        ),
        terminal=prepared.terminal_reason is not None,
    )
    expected_reason = _critic_reason(proposal, view, remaining)
    if prepared.attempt_started_at < view.observed_at:
        expected_reason = "proposal_time_invalid"
    if expected_registration >= view.first_eligible_completed_bar_at:
        expected_reason = "forward_probe_not_future_only"
    if (
        prepared.cycle_id != cycle_id
        or prepared.evidence_sha256 != evidence_sha256
        or prepared.branch_index != branch
        or prepared.market_id != view.market_id.value
        or prepared.search_budget != view.search_budget
        or prepared.remaining_budget_before != remaining
        or prepared.search_budget_debit != _search_budget_debit(proposal, remaining)
        or prepared.terminal_reason != expected_reason
        or (prepared.family, prepared.version, prepared.preregistration) != expected
    ):
        raise DayDiscoveryError("prepared_branch_contract_invalid")


def _day_critique(proposal: ProposedHypothesis, view: DayDiscoveryEvidenceView, remaining: int) -> CritiqueReport:
    reason = _critic_reason(proposal, view, remaining)
    return CritiqueReport(
        () if reason is None else (Objection(ObjectionKind.SOURCE_FIDELITY, Severity.BLOCKING, reason),)
    )


def _critique_terminal_reason(critique: CritiqueReport) -> str | None:
    blocking = tuple(item for item in critique.objections if item.severity is Severity.BLOCKING)
    if not blocking:
        return None
    reasons = tuple(item.evidence for item in blocking if item.evidence in TERMINAL_REASONS)
    return next((reason for reason in reasons if reason != "critic_rejected"), "critic_rejected")


def _record_terminal(
    ledger: ExperimentLedgerStore,
    attempt_id: str,
    branch: int,
    version: HypothesisVersion,
    artifact_ref: str,
    view: DayDiscoveryEvidenceView,
    reason: str,
    started_at: dt.datetime,
    finished_at: dt.datetime,
    bound_at: dt.datetime,
    search_budget_debit: int,
    multiple_testing_budget: int,
) -> None:
    attempt = ResearchAttempt(
        attempt_id=attempt_id,
        hypothesis_id=version.hypothesis_version_id,
        branch_index=branch,
        input_hashes=(view.data_manifest_sha256,),
        code_sha256=version.code_sha256,
        data_manifest_sha256=view.data_manifest_sha256,
        started_at=started_at,
        finished_at=finished_at,
        status=AttemptStatus.FAILED,
        artifact_refs=(),
        error_class=reason,
        max_cpu_seconds=view.resource_limits.cpu_seconds,
    )
    binding = _binding(
        attempt,
        version,
        artifact_ref,
        bound_at,
        search_budget_debit,
        multiple_testing_budget,
    )
    with ledger.writer() as writer:
        _ = writer.append_strategy_research_attempt(attempt)
        _ = writer.register_day_research_attempt_binding(binding)


def _binding(
    attempt: ResearchAttempt,
    version: HypothesisVersion,
    artifact_ref: str,
    bound_at: dt.datetime,
    search_budget_debit: int,
    multiple_testing_budget: int,
) -> DayResearchAttemptBinding:
    return _binding_for(
        attempt.attempt_id,
        version,
        artifact_ref,
        bound_at,
        search_budget_debit,
        multiple_testing_budget,
    )


def _binding_for(
    attempt_id: str,
    version: HypothesisVersion,
    artifact_ref: str,
    bound_at: dt.datetime,
    search_budget_debit: int,
    multiple_testing_budget: int,
) -> DayResearchAttemptBinding:
    payload = {
        "binding_id": "",
        "attempt_id": attempt_id,
        "market_id": version.market_id,
        "hypothesis_version_id": version.hypothesis_version_id,
        "artifact_ref": artifact_ref,
        "multiple_testing_family": version.multiple_testing_family,
        "multiple_testing_budget": multiple_testing_budget,
        "search_budget_debit": search_budget_debit,
        "bound_at": bound_at,
    }
    return DayResearchAttemptBinding.model_validate(
        payload | {"binding_id": DayResearchAttemptBinding.canonical_id_for(payload)}
    )


def _parameter_combination_demand(proposal: ProposedHypothesis) -> int:
    parameters = proposal.strategy_draft.free_parameters
    if (
        len(set(parameters)) > 12
        or any(not _canonical_ai_text(value) or len(value) > 80 for value in parameters)
    ):
        return 2
    return 2 ** len(set(parameters))


def _search_budget_debit(proposal: ProposedHypothesis, remaining: int) -> int:
    return min(_parameter_combination_demand(proposal), remaining)


def _capsule_request(
    version: HypothesisVersion,
    binding: DayResearchAttemptBinding,
    artifact_id: str,
    view: DayDiscoveryEvidenceView,
    artifact_store: GeneratedStrategyArtifactStore,
    sandbox: GeneratedStrategySandbox,
    published_at: dt.datetime,
) -> DayStrategyCapsuleRequest:
    limits = CapsuleResourceLimits(**asdict(view.resource_limits))
    return DayStrategyCapsuleRequest(
        hypothesis_version_id=version.hypothesis_version_id,
        attempt_binding_id=binding.binding_id,
        market_id=view.market_id,
        artifact_kind=CapsuleArtifactKind.GENERATED_PYTHON,
        artifact_ref=preregistered_attempted_artifact_ref(version.code_sha256),
        artifact_sha256=version.code_sha256,
        generated_artifact_id=artifact_id,
        evaluation_cadence=version.evaluation_cadence,
        evidence_schema=view.evidence_schema,
        entry_rule=version.entry_rule,
        exit_rule=version.exit_rule,
        stop_rule=version.stop_rule,
        target_rule="host_projects_preregistered_targets",
        cost_model=version.cost_model,
        slippage_model_id="bounded_intraday_slippage_v1",
        resource_limits=limits,
        risk_policy_ref="risk-policy://day-research/v1",
        protocol_version=1,
        protocol_sha256=generated_protocol_bundle_sha256(),
        evaluator_sha256=generated_evaluator_bundle_sha256(),
        published_at=published_at,
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


def _canonical_view(view: DayDiscoveryEvidenceView) -> str:
    return json.dumps(
        view.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _read_cycle_receipt(
    path: Path,
    cycle_id: str,
    evidence_sha256: str,
) -> DayDiscoveryCycleResult | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        raw = read_private_text(path)
        receipt = DayDiscoveryCycleReceipt.model_validate_json(raw)
    except (DayDiscoveryError, InvalidPrivateImmutableFileError, ValueError):
        raise DayDiscoveryError("cycle_receipt_invalid") from None
    canonical = json.dumps(
        receipt.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if raw != canonical:
        raise DayDiscoveryError("cycle_receipt_noncanonical")
    if receipt.cycle_id != cycle_id:
        raise DayDiscoveryError("cycle_receipt_identity_conflict")
    if receipt.evidence_sha256 != evidence_sha256:
        raise DayDiscoveryError("cycle_evidence_identity_conflict")
    return receipt.result


@contextmanager
def _cycle_receipt_lease(root: Path, cycle_id: str) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    lock_path = root / f".{cycle_id}.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise DayDiscoveryError("cycle_receipt_lock_invalid")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError:
        raise DayDiscoveryError("cycle_receipt_lock_invalid") from None
    finally:
        os.close(descriptor)


def _bounded_prompt_view(view: DayDiscoveryEvidenceView, remaining: int) -> str:
    prompt_view = DayDiscoveryPromptView(
        market_id=view.market_id,
        trigger_kind=view.trigger_kind,
        observed_at=view.observed_at,
        completed_bar_at=view.completed_bar_at,
        first_eligible_completed_bar_at=view.first_eligible_completed_bar_at,
        universe_snapshot_sha256=_sha(view.universe_snapshot_id),
        universe_snapshot_at=view.universe_snapshot_at,
        source_refs_sha256=_sha("\x1f".join(view.source_refs)),
        source_ref_count=len(view.source_refs),
        evidence_schema_sha256=_sha("\x1f".join(view.evidence_schema)),
        evidence_schema_count=len(view.evidence_schema),
        data_manifest_sha256=view.data_manifest_sha256,
        replay_bars=tuple(
            DayDiscoveryPromptBar(
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                prior_close=bar.prior_close,
                average_daily_volume=bar.average_daily_volume,
                spread_bps=bar.spread_bps,
            )
            for bar in view.replay_bars
        ),
        search_budget=view.search_budget,
        budget_debits_used=view.budget_debits_used,
        remaining_budget=remaining,
        cursor_sha256=_sha(view.cursor),
        previous_failure=view.previous_failure,
        existing_semantic_hashes_sha256=_sha("\x1f".join(view.existing_semantic_hashes)),
        existing_semantic_hash_count=len(view.existing_semantic_hashes),
        feedback=view.feedback,
        resource_limits=DayDiscoveryPromptLimits(**asdict(view.resource_limits)),
    )
    return json.dumps(
        prompt_view.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _require_safe_day_context(context: ResearcherContext) -> None:
    payload = json.dumps(
        {
            "existing_hypothesis_texts": context.existing_hypothesis_texts,
            "failure_digest": asdict(context.failure_digest),
            "regime_context": context.regime_context,
            "sources": tuple(source.model_dump(mode="json") for source in context.sources),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if contains_sensitive_text((payload,)):
        raise DayDiscoveryError("day_prompt_sensitive_context")


def _safe_reason_token(value: str | None) -> str | None:
    forbidden = ("account", "api_key", "credential", "provider", "secret", "token")
    if value is not None and (
        not 1 <= len(value) <= 80
        or any(not (character.islower() or character.isdigit() or character == "_") for character in value)
        or any(term in value for term in forbidden)
    ):
        raise DayDiscoveryError("feedback_reason_invalid")
    return value


def _require_aware_utc(value: dt.datetime, reason: str) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DayDiscoveryError(reason)
    return value.astimezone(dt.UTC)


def _cycle_time(clock: Callable[[], dt.datetime] | None, floor: dt.datetime) -> dt.datetime:
    current = floor if clock is None else _require_aware_utc(clock(), "cycle_clock_naive")
    return max(current, floor + dt.timedelta(microseconds=1))


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
