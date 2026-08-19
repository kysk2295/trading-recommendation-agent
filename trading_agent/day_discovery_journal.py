from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.day_hypothesis_models import HypothesisFamily, HypothesisVersion
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
)
from trading_agent.experiment_scope_models import ExperimentScope
from trading_agent.lane_identity_models import LaneId
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.researcher_agent import (
    CandidateStrategyDraft,
    LlmCallReceipt,
    ProposedHypothesis,
)
from trading_agent.strategy_research_models import PreregistrationManifest

TERMINAL_REASONS = frozenset(
    {
        "artifact_publication_failed",
        "budget_exhausted",
        "compile_failed",
        "critic_rejected",
        "contract_invalid",
        "forward_probe_not_future_only",
        "methodology_missing",
        "model_call_interrupted",
        "nondeterministic",
        "point_in_time_leakage",
        "proposal_time_invalid",
        "sandbox_failed",
        "semantic_duplicate",
        "unconstructible",
    }
)


@dataclass(frozen=True, slots=True)
class InvalidDayDiscoveryJournalError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class PreparedLlmReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int | None
    temperature: float
    called_at: dt.datetime

    @field_validator("called_at")
    @classmethod
    def aware_utc(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidDayDiscoveryJournalError("prepared_time_invalid")
        return value.astimezone(dt.UTC)

    def receipt(self) -> LlmCallReceipt:
        return LlmCallReceipt(**self.model_dump(mode="python"))


class PreparedStrategyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_code: str
    free_parameters: tuple[str, ...]
    methodology_tags: tuple[str, ...]

    def draft(self) -> CandidateStrategyDraft:
        return CandidateStrategyDraft(**self.model_dump(mode="python"))


class DayDiscoveryPreparedBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_index: int = Field(ge=0, le=2)
    market_id: str
    search_budget: int = Field(ge=1, le=10_000)
    remaining_budget_before: int = Field(ge=1, le=10_000)
    proposal_card: dict[str, object]
    cited_sources: tuple[ResearchSource, ...]
    llm_receipt: PreparedLlmReceipt
    strategy_draft: PreparedStrategyDraft
    terminal_reason: str | None
    family: HypothesisFamily
    version: HypothesisVersion
    preregistration: PreregistrationManifest
    attempt_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_started_at: dt.datetime
    attempt_finished_at: dt.datetime
    bound_at: dt.datetime
    published_at: dt.datetime
    search_budget_debit: int = Field(ge=1, le=10_000)

    @field_validator("attempt_started_at", "attempt_finished_at", "bound_at", "published_at")
    @classmethod
    def aware_utc(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidDayDiscoveryJournalError("prepared_time_invalid")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_prepared_identity(self) -> Self:
        if (
            self.version.family_id != self.family.family_id
            or self.preregistration.hypothesis.hypothesis_id
            != self.version.hypothesis_version_id
            or self.attempt_started_at > self.attempt_finished_at
            or self.attempt_finished_at >= self.bound_at
            or self.bound_at >= self.published_at
            or self.attempt_started_at != self.llm_receipt.called_at
            or self.attempt_id
            != _sha(f"{self.cycle_id}:{self.branch_index}:{self.version.hypothesis_version_id}")
            or self.search_budget_debit > self.remaining_budget_before
            or (self.terminal_reason is not None and self.terminal_reason not in TERMINAL_REASONS)
        ):
            raise InvalidDayDiscoveryJournalError("prepared_branch_identity_invalid")
        return self

    def proposal(self) -> ProposedHypothesis:
        payload = dict(self.proposal_card)
        hypothesis = payload.get("hypothesis")
        if not isinstance(hypothesis, dict):
            raise InvalidDayDiscoveryJournalError("prepared_branch_proposal_invalid")
        registration = dict(hypothesis)
        experiment_scope = ExperimentScope.model_validate(
            registration["experiment_scope"]
        )
        primary_lane = LaneId(str(registration["primary_lane"]))
        source_registered_at = dt.datetime.fromisoformat(
            str(registration["source_registered_at"])
        )
        ledger_recorded_at = dt.datetime.fromisoformat(
            str(registration["ledger_recorded_at"])
        )
        typed_registration = HypothesisRegistration.model_construct(
            schema_version=1,
            hypothesis_id=str(registration["hypothesis_id"]),
            experiment_scope=experiment_scope,
            experiment_scope_key=str(registration["experiment_scope_key"]),
            primary_lane=primary_lane,
            hypothesis=str(registration["hypothesis"]),
            falsification_rule=str(registration["falsification_rule"]),
            source_registered_at=source_registered_at,
            ledger_recorded_at=ledger_recorded_at,
        )
        keys = payload.get("research_source_keys")
        if not isinstance(keys, list):
            raise InvalidDayDiscoveryJournalError("prepared_branch_proposal_invalid")
        research_source_keys = tuple(str(value) for value in keys)
        return ProposedHypothesis(
            card=ResearchHypothesisCard.model_construct(
                schema_version=1,
                hypothesis=typed_registration,
                research_source_keys=research_source_keys,
                economic_mechanism=str(payload["economic_mechanism"]),
                counterfactual_baseline=str(payload["counterfactual_baseline"]),
            ),
            cited_sources=self.cited_sources,
            llm_receipt=self.llm_receipt.receipt(),
            strategy_draft=self.strategy_draft.draft(),
        )


class DayDiscoveryBranchReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_index: int = Field(ge=0, le=2)
    market_id: str
    search_budget: int = Field(ge=1, le=10_000)
    remaining_budget_before: int = Field(ge=1, le=10_000)
    reserved_at: dt.datetime

    @field_validator("reserved_at")
    @classmethod
    def aware_utc(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidDayDiscoveryJournalError("reservation_time_invalid")
        return value.astimezone(dt.UTC)


class DayDiscoveryBranchResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_index: int = Field(ge=0, le=2)
    attempt_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: str
    terminal_reason: str | None
    artifact_id: str | None = None
    artifact_ref: str | None = None
    capsule: StrategyCapsule | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        if self.outcome not in {"success", "terminal"}:
            raise InvalidDayDiscoveryJournalError("resolution_outcome_invalid")
        if self.outcome == "success":
            if (
                self.terminal_reason is not None
                or self.artifact_id is None
                or self.artifact_ref is None
                or self.capsule is None
            ):
                raise InvalidDayDiscoveryJournalError("resolution_identity_invalid")
        elif self.terminal_reason not in TERMINAL_REASONS or self.capsule is not None:
            raise InvalidDayDiscoveryJournalError("resolution_reason_invalid")
        return self


def prepared_branch_path(root: Path, cycle_id: str, branch_index: int) -> Path:
    return root / f"{cycle_id}.prepared.{branch_index}.json"


def reservation_path(root: Path, cycle_id: str, branch_index: int) -> Path:
    return root / f"{cycle_id}.reservation.{branch_index}.json"


def resolution_path(root: Path, cycle_id: str, branch_index: int) -> Path:
    return root / f"{cycle_id}.resolution.{branch_index}.json"


def publish_prepared_branch(path: Path, prepared: DayDiscoveryPreparedBranch) -> None:
    try:
        _ = publish_private_immutable_text(path, _canonical(prepared))
    except InvalidPrivateImmutableFileError:
        raise InvalidDayDiscoveryJournalError("prepared_branch_publication_failed") from None


def publish_reservation(path: Path, reservation: DayDiscoveryBranchReservation) -> None:
    try:
        _ = publish_private_immutable_text(path, _canonical(reservation))
    except InvalidPrivateImmutableFileError:
        raise InvalidDayDiscoveryJournalError("reservation_publication_failed") from None


def publish_resolution(path: Path, resolution: DayDiscoveryBranchResolution) -> None:
    try:
        _ = publish_private_immutable_text(path, _canonical(resolution))
    except InvalidPrivateImmutableFileError:
        raise InvalidDayDiscoveryJournalError("resolution_publication_failed") from None


def read_prepared_branch(
    path: Path,
    cycle_id: str,
    evidence_sha256: str,
    branch_index: int,
) -> DayDiscoveryPreparedBranch | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        raw = read_private_text(path)
        prepared = DayDiscoveryPreparedBranch.model_validate_json(raw)
    except (
        InvalidDayDiscoveryJournalError,
        InvalidPrivateImmutableFileError,
        ValueError,
    ):
        raise InvalidDayDiscoveryJournalError("prepared_branch_invalid") from None
    if raw != _canonical(prepared):
        raise InvalidDayDiscoveryJournalError("prepared_branch_noncanonical")
    if (
        prepared.cycle_id != cycle_id
        or prepared.evidence_sha256 != evidence_sha256
        or prepared.branch_index != branch_index
    ):
        raise InvalidDayDiscoveryJournalError("prepared_branch_identity_conflict")
    return prepared


def _read_model(path: Path, model: type[BaseModel], reason: str) -> BaseModel | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        raw = read_private_text(path)
        value = model.model_validate_json(raw)
    except (InvalidDayDiscoveryJournalError, InvalidPrivateImmutableFileError, ValueError):
        raise InvalidDayDiscoveryJournalError(reason) from None
    if raw != _canonical(value):
        raise InvalidDayDiscoveryJournalError(reason) from None
    return value


def read_reservation(path: Path) -> DayDiscoveryBranchReservation | None:
    value = _read_model(path, DayDiscoveryBranchReservation, "reservation_invalid")
    return None if value is None else DayDiscoveryBranchReservation.model_validate(value)


def read_resolution(path: Path) -> DayDiscoveryBranchResolution | None:
    value = _read_model(path, DayDiscoveryBranchResolution, "resolution_invalid")
    return None if value is None else DayDiscoveryBranchResolution.model_validate(value)


def _canonical(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


__all__ = (
    "TERMINAL_REASONS",
    "DayDiscoveryBranchReservation",
    "DayDiscoveryBranchResolution",
    "DayDiscoveryPreparedBranch",
    "InvalidDayDiscoveryJournalError",
    "PreparedLlmReceipt",
    "PreparedStrategyDraft",
    "prepared_branch_path",
    "publish_prepared_branch",
    "publish_reservation",
    "publish_resolution",
    "read_prepared_branch",
    "read_reservation",
    "read_resolution",
    "reservation_path",
    "resolution_path",
)
