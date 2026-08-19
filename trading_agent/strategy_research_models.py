from __future__ import annotations

import datetime as dt
import math
from functools import reduce
from operator import mul
from typing import Literal, Self, assert_never

from pydantic import Field, model_validator

from trading_agent.strategy_research_types import (
    CanonicalModel,
    EvidenceKind,
    EvidenceUse,
    ExpectedDirection,
    HypothesisStatus,
    LiveEligibilityPolicy,
    ResearchAgentId,
    StrategyResearchContractError,
    aware,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class EvidenceRef(CanonicalModel):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_kind: EvidenceKind
    evidence_use: EvidenceUse
    live_eligibility_policy: LiveEligibilityPolicy
    as_of: dt.datetime
    available_at: dt.datetime
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_ref(self) -> Self:
        if not aware(self.as_of) or not aware(self.available_at) or self.available_at < self.as_of:
            raise StrategyResearchContractError
        match self.source_kind:
            case EvidenceKind.REAL:
                if (
                    self.evidence_use is not EvidenceUse.RESEARCH
                    or self.live_eligibility_policy is not LiveEligibilityPolicy.TASK3_CURRENT_SESSION_GATE_REQUIRED
                ):
                    raise StrategyResearchContractError
            case EvidenceKind.FIXTURE | EvidenceKind.SYNTHETIC | EvidenceKind.REPLAY | EvidenceKind.BACKTEST:
                if (
                    self.evidence_use is not EvidenceUse.WIRING_ONLY
                    or self.live_eligibility_policy is not LiveEligibilityPolicy.WIRING_ONLY_NO_LIVE_USE
                ):
                    raise StrategyResearchContractError
            case unreachable:
                assert_never(unreachable)
        return self


class EvidenceObservation(CanonicalModel):
    observation_id: str = Field(min_length=1)
    owner_agent_id: ResearchAgentId
    observed_at: dt.datetime
    as_of: dt.datetime
    universe_definition: str = Field(min_length=1)
    universe_snapshot_id: str = Field(min_length=1)
    universe_observed_at: dt.datetime
    predictor_formula: str = Field(min_length=1)
    predictor_observed_at: dt.datetime
    target_matures_at: dt.datetime
    coverage_fraction: float = Field(gt=0, le=1)
    source_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        times = (
            self.observed_at,
            self.as_of,
            self.universe_observed_at,
            self.predictor_observed_at,
            self.target_matures_at,
        )
        source_ids = tuple(item.source_id for item in self.source_refs)
        if (
            not all(aware(value) for value in times)
            or not math.isfinite(self.coverage_fraction)
            or not self.as_of <= self.universe_observed_at <= self.predictor_observed_at <= self.observed_at
            or self.target_matures_at <= self.observed_at
            or any(
                item.as_of > self.universe_observed_at or item.available_at > self.predictor_observed_at
                for item in self.source_refs
            )
            or len(source_ids) != len(set(source_ids))
        ):
            raise StrategyResearchContractError
        return self


class ResearchPeriod(CanonicalModel):
    start: dt.datetime
    end: dt.datetime

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if not aware(self.start) or not aware(self.end) or self.end <= self.start:
            raise StrategyResearchContractError
        return self


class SealedHoldoutRef(CanonicalModel):
    seal_id: str = Field(min_length=1)
    commitment_sha256: str = Field(pattern=_SHA256_PATTERN)
    sealed_at: dt.datetime
    owner: str = Field(min_length=1)
    access_policy: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        if not aware(self.sealed_at):
            raise StrategyResearchContractError
        return self


class FreeParameter(CanonicalModel):
    name: str = Field(min_length=1)
    candidate_values: tuple[float, ...] = Field(min_length=2, max_length=32)
    lower_bound: float
    upper_bound: float

    @model_validator(mode="after")
    def validate_parameter(self) -> Self:
        if (
            not all(math.isfinite(value) for value in (*self.candidate_values, self.lower_bound, self.upper_bound))
            or self.lower_bound >= self.upper_bound
            or self.candidate_values != tuple(sorted(set(self.candidate_values)))
            or any(not self.lower_bound <= value <= self.upper_bound for value in self.candidate_values)
        ):
            raise StrategyResearchContractError
        return self


class SearchBudget(CanonicalModel):
    max_parameter_combinations: int = Field(ge=1, le=10_000)
    max_attempts: int = Field(ge=1, le=10_000)
    max_cpu_seconds: int = Field(ge=1, le=86_400)


class TargetHorizon(CanonicalModel):
    duration: dt.timedelta = Field(gt=dt.timedelta(0), le=dt.timedelta(days=3_650))


class ImmutableHypothesis(CanonicalModel):
    hypothesis_id: str = Field(min_length=1)
    parent_hypothesis_id: str | None
    search_family_id: str = Field(min_length=1)
    agent_id: ResearchAgentId
    owner_family: str = Field(min_length=1)
    lane_id: str = Field(min_length=1)
    created_at: dt.datetime
    created_by: str = Field(min_length=1)
    source_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    evidence_hashes: tuple[str, ...] = Field(min_length=1)
    evidence_use: EvidenceUse
    observation: EvidenceObservation
    point_in_time_policy: str = Field(min_length=1)
    universe_definition: str = Field(min_length=1)
    universe_snapshot_id: str = Field(min_length=1)
    universe_observed_at: dt.datetime
    instrument_scope: str = Field(min_length=1)
    predictor_formula: str = Field(min_length=1)
    sampling_timestamp: dt.datetime
    target_formula: str = Field(min_length=1)
    target_horizon: TargetHorizon
    target_matures_at: dt.datetime
    expected_direction: ExpectedDirection
    entry_rule: str = Field(min_length=1)
    exit_rule: str = Field(min_length=1)
    stop_rule: str = Field(min_length=1)
    invalidation_rule: str = Field(min_length=1)
    economic_mechanism: str = Field(min_length=1)
    alternative_explanations: tuple[str, ...] = Field(min_length=1)
    counterfactual_baseline: str = Field(min_length=1)
    baseline_id: str = Field(min_length=1)
    cost_model_id: str = Field(min_length=1)
    slippage_model_id: str = Field(min_length=1)
    primary_metric: str = Field(min_length=1)
    secondary_metrics: tuple[str, ...]
    falsification_rule: str = Field(min_length=1)
    free_parameters: tuple[FreeParameter, ...] = Field(max_length=12)
    search_budget: SearchBudget
    minimum_observations: int = Field(ge=20)
    power_or_ci_gate: str = Field(min_length=1)
    multiple_testing_family: str = Field(min_length=1)
    max_attempts: int = Field(ge=1, le=10_000)
    train_period: ResearchPeriod
    validation_period: ResearchPeriod
    holdout_period_sealed_ref: SealedHoldoutRef
    holdout_access_policy: str = Field(min_length=1)
    model_hash: str = Field(pattern=_SHA256_PATTERN)
    prompt_hash: str = Field(pattern=_SHA256_PATTERN)
    protocol_version: str = Field(min_length=1)
    code_sha256: str = Field(pattern=_SHA256_PATTERN)
    data_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: HypothesisStatus
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_hypothesis(self) -> Self:
        available_combinations = reduce(mul, (len(item.candidate_values) for item in self.free_parameters), 1)
        payload_hashes = tuple(item.payload_sha256 for item in self.source_refs)
        real_evidence = all(item.source_kind is EvidenceKind.REAL for item in self.source_refs)
        wiring_evidence = all(item.source_kind is not EvidenceKind.REAL for item in self.source_refs)
        evidence_mode_valid = (self.evidence_use is EvidenceUse.RESEARCH and real_evidence) or (
            self.evidence_use is EvidenceUse.WIRING_ONLY and wiring_evidence
        )
        if (
            not aware(self.created_at)
            or not aware(self.sampling_timestamp)
            or self.created_at < self.observation.observed_at
            or self.agent_id is not self.observation.owner_agent_id
            or self.source_refs != self.observation.source_refs
            or self.evidence_hashes != payload_hashes
            or not evidence_mode_valid
            or self.universe_definition != self.observation.universe_definition
            or self.universe_snapshot_id != self.observation.universe_snapshot_id
            or self.universe_observed_at != self.observation.universe_observed_at
            or self.predictor_formula != self.observation.predictor_formula
            or self.sampling_timestamp != self.observation.predictor_observed_at
            or self.target_matures_at != self.observation.target_matures_at
            or self.target_matures_at <= self.created_at
            or self.target_matures_at - self.sampling_timestamp != self.target_horizon.duration
            or self.train_period.end >= self.validation_period.start
            or self.holdout_period_sealed_ref.sealed_at >= self.train_period.start
            or self.search_budget.max_parameter_combinations > available_combinations
            or self.search_budget.max_attempts > self.search_budget.max_parameter_combinations
            or self.max_attempts != self.search_budget.max_attempts
            or "naive normal" in self.power_or_ci_gate.casefold()
            or len(set(self.alternative_explanations)) != len(self.alternative_explanations)
            or len(set(self.secondary_metrics)) != len(self.secondary_metrics)
        ):
            raise StrategyResearchContractError
        return self


class PreregistrationManifest(CanonicalModel):
    hypothesis: ImmutableHypothesis
    hypothesis_sha256: str = Field(pattern=_SHA256_PATTERN)
    preregistered_at: dt.datetime
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @classmethod
    def from_hypothesis(cls, hypothesis: ImmutableHypothesis, *, preregistered_at: dt.datetime) -> Self:
        return cls(
            hypothesis=hypothesis,
            hypothesis_sha256=hypothesis.content_sha256,
            preregistered_at=preregistered_at,
        )

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if (
            not aware(self.preregistered_at)
            or self.preregistered_at < self.hypothesis.created_at
            or self.hypothesis.status is not HypothesisStatus.PREREGISTERED
            or self.hypothesis_sha256 != self.hypothesis.content_sha256
        ):
            raise StrategyResearchContractError
        return self


__all__ = (
    "EvidenceObservation",
    "EvidenceRef",
    "FreeParameter",
    "ImmutableHypothesis",
    "PreregistrationManifest",
    "ResearchPeriod",
    "SealedHoldoutRef",
    "SearchBudget",
    "TargetHorizon",
)
