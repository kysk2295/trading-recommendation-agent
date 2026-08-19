from __future__ import annotations

import datetime as dt
import hashlib
import math
from dataclasses import dataclass
from typing import Protocol, Self, assert_never

from pydantic import Field, model_validator

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.strategy_research_attempt_selection import AttemptSummary
from trading_agent.strategy_research_experiment_models import FrozenScienceProtocol, ParameterValue
from trading_agent.strategy_research_ledger import ExactHoldoutMetric, HoldoutReveal, StrategyResearchLedgerError
from trading_agent.strategy_research_models import ImmutableHypothesis
from trading_agent.strategy_research_results import TerminalResearchResult
from trading_agent.strategy_research_statistics import (
    BootstrapInterval,
    BootstrapPolicy,
    fixed_seed_resampled_mean,
)
from trading_agent.strategy_research_types import (
    CanonicalModel,
    ExpectedDirection,
    SafeTerminalReason,
    TerminalOutcome,
)


class HoldoutBranch(CanonicalModel):
    parameter_values: tuple[ParameterValue, ...] = Field(min_length=1)
    values: tuple[float, ...] = Field(min_length=1)
    cluster_keys: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_branch(self) -> Self:
        names = tuple(item.name for item in self.parameter_values)
        if (
            len(names) != len(set(names))
            or not all(math.isfinite(value) for value in self.values)
            or len(self.cluster_keys) != len(self.values)
            or any(not key for key in self.cluster_keys)
        ):
            raise StrategyResearchLedgerError("holdout_branch_invalid")
        return self


class SealedHoldoutPayload(CanonicalModel):
    reviewer_id: str = Field(min_length=1)
    branches: tuple[HoldoutBranch, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        combinations = tuple(_combination(item.parameter_values) for item in self.branches)
        if len(combinations) != len(set(combinations)):
            raise StrategyResearchLedgerError("holdout_branch_conflict")
        return self


@dataclass(frozen=True, slots=True)
class HoldoutReviewRequest:
    hypothesis: ImmutableHypothesis
    protocol: FrozenScienceProtocol
    selected: AttemptSummary
    evaluated_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SanitizedHoldoutReview:
    reveal_id: str
    terminal: TerminalResearchResult


class HoldoutReviewer(Protocol):
    def review_and_reveal(
        self,
        store: ExperimentLedgerStore,
        request: HoldoutReviewRequest,
    ) -> SanitizedHoldoutReview: ...


@dataclass(frozen=True, slots=True)
class SealedHoldoutReviewer:
    _payload: SealedHoldoutPayload

    @classmethod
    def from_payload(cls, payload: SealedHoldoutPayload) -> Self:
        return cls(payload)

    def review_and_reveal(
        self,
        store: ExperimentLedgerStore,
        request: HoldoutReviewRequest,
    ) -> SanitizedHoldoutReview:
        seal = request.hypothesis.holdout_period_sealed_ref
        if self._payload.content_sha256 != seal.commitment_sha256:
            raise StrategyResearchLedgerError("holdout_commitment_mismatch")
        selected_combination = _combination(request.selected.parameter_values)
        matches = tuple(
            item for item in self._payload.branches if _combination(item.parameter_values) == selected_combination
        )
        if len(matches) != 1:
            raise StrategyResearchLedgerError("selected_holdout_branch_missing")
        interval = fixed_seed_resampled_mean(
            matches[0].values,
            BootstrapPolicy(
                repetitions=request.protocol.bootstrap_repetitions,
                seed=request.protocol.bootstrap_seed,
                familywise_alpha=request.protocol.familywise_alpha,
                adjustment_tests=request.protocol.adjustment_tests,
            ),
            request.protocol.resampling_method,
            matches[0].cluster_keys,
        )
        outcome, reasons = _terminal_decision(
            request,
            interval,
            len(matches[0].values),
        )
        safe_hash = _sha(f"{request.protocol.protocol_id}:{request.selected.attempt_id}:terminal:{outcome.value}")
        terminal = TerminalResearchResult(
            result_id=f"terminal-{safe_hash}",
            hypothesis_id=request.hypothesis.hypothesis_id,
            owner_agent_id=request.hypothesis.agent_id,
            outcome=outcome,
            reason_codes=reasons,
            artifact_refs=(f"artifact://safe/{safe_hash}",),
            evaluated_at=request.evaluated_at,
        )
        reveal_id = f"reveal-{_sha(f'{request.protocol.protocol_id}:{request.selected.attempt_id}:reveal')}"
        reveal = HoldoutReveal(
            reveal_id=reveal_id,
            hypothesis_id=request.hypothesis.hypothesis_id,
            seal_id=seal.seal_id,
            commitment_sha256=seal.commitment_sha256,
            reviewer_id=self._payload.reviewer_id,
            exact_metrics=(
                ExactHoldoutMetric(
                    name=request.hypothesis.primary_metric,
                    value=interval.estimate,
                    lower=interval.lower,
                    upper=interval.upper,
                ),
            ),
            sanitized_result=terminal,
            revealed_at=request.evaluated_at,
        )
        with store.writer() as writer:
            _ = writer.reveal_strategy_research_holdout(reveal)
        return SanitizedHoldoutReview(reveal_id, terminal)


def _terminal_decision(
    request: HoldoutReviewRequest,
    interval: BootstrapInterval,
    observations: int,
) -> tuple[TerminalOutcome, tuple[SafeTerminalReason, ...]]:
    if observations < request.protocol.minimum_observations:
        return TerminalOutcome.INCONCLUSIVE, (SafeTerminalReason.INSUFFICIENT_OBSERVATIONS,)
    if interval.width > request.protocol.maximum_interval_width:
        return TerminalOutcome.INCONCLUSIVE, (SafeTerminalReason.CI_WIDTH_TOO_WIDE,)
    match request.hypothesis.expected_direction:
        case ExpectedDirection.POSITIVE:
            supported, refuted = interval.lower > 0, interval.upper <= 0
        case ExpectedDirection.NEGATIVE:
            supported, refuted = interval.upper < 0, interval.lower >= 0
        case ExpectedDirection.TWO_SIDED:
            supported, refuted = interval.lower > 0 or interval.upper < 0, False
        case unreachable:
            assert_never(unreachable)
    if supported:
        return TerminalOutcome.SUPPORTED, (SafeTerminalReason.PREREGISTERED_SUPPORT_MET,)
    if refuted:
        return TerminalOutcome.REFUTED, (SafeTerminalReason.PREREGISTERED_FALSIFICATION_MET,)
    return TerminalOutcome.INCONCLUSIVE, (SafeTerminalReason.CI_WIDTH_TOO_WIDE,)


def _combination(values: tuple[ParameterValue, ...]) -> tuple[tuple[str, float], ...]:
    return tuple(sorted((item.name, item.value) for item in values))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = (
    "HoldoutBranch",
    "HoldoutReviewRequest",
    "HoldoutReviewer",
    "SanitizedHoldoutReview",
    "SealedHoldoutPayload",
    "SealedHoldoutReviewer",
)
