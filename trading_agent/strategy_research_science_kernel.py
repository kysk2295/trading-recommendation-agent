from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_research_attempt_selection import prepare_search
from trading_agent.strategy_research_experiment_models import (
    FrozenScienceProtocol,
    ScienceCycleResult,
    ScienceExperiment,
)
from trading_agent.strategy_research_holdout_reviewer import HoldoutReviewer, HoldoutReviewRequest
from trading_agent.strategy_research_ledger import StrategyResearchLedgerError
from trading_agent.strategy_research_methodologies import strategy_research_methodology
from trading_agent.strategy_research_models import ImmutableHypothesis, PreregistrationManifest
from trading_agent.strategy_research_policy import require_validated_online_error_control
from trading_agent.strategy_research_types import HypothesisStatus

_BOOTSTRAP_REPETITIONS: Final = 2_000
_FAMILYWISE_ALPHA: Final = 0.05
_MAXIMUM_INTERVAL_WIDTH: Final = 0.02


class BarOutcome(StrEnum):
    STOP = "stop"
    TARGET = "target"
    NEITHER = "neither"


@dataclass(frozen=True, slots=True)
class CompletedBarRange:
    low: float
    high: float


@dataclass(frozen=True, slots=True)
class StopTargetThresholds:
    stop: float
    target: float


def resolve_same_bar_outcome(bar: CompletedBarRange, thresholds: StopTargetThresholds) -> BarOutcome:
    if bar.low <= thresholds.stop:
        return BarOutcome.STOP
    if bar.high >= thresholds.target:
        return BarOutcome.TARGET
    return BarOutcome.NEITHER


def validate_market_time_series_online_claim(
    *,
    claimed: bool,
    evaluator_version: str | None,
    validation_artifact_ref: str | None,
) -> None:
    require_validated_online_error_control(
        claimed=claimed,
        evaluator_version=evaluator_version,
        validation_artifact_ref=validation_artifact_ref,
    )


class ScienceKernel:
    __slots__ = ("_reviewer", "_store")

    def __init__(self, store: ExperimentLedgerStore, reviewer: HoldoutReviewer) -> None:
        self._store = store
        self._reviewer = reviewer

    def run(self, draft: ImmutableHypothesis, experiment: ScienceExperiment) -> ScienceCycleResult:
        hypothesis = _preregistered_hypothesis(draft)
        protocol = _protocol(hypothesis)
        search = prepare_search(hypothesis, protocol, experiment)
        manifest = PreregistrationManifest.from_hypothesis(
            hypothesis,
            preregistered_at=max(hypothesis.created_at, experiment.started_at - dt.timedelta(microseconds=1)),
        )
        with self._store.writer() as writer:
            _ = writer.register_strategy_research(manifest)
            for attempt in search.attempts:
                _ = writer.append_strategy_research_attempt(attempt)
        if search.selected is None:
            raise StrategyResearchLedgerError("eligible_attempt_missing")
        review = self._reviewer.review_and_reveal(
            self._store,
            HoldoutReviewRequest(
                hypothesis=hypothesis,
                protocol=protocol,
                selected=search.selected,
                evaluated_at=experiment.started_at + dt.timedelta(seconds=len(experiment.attempts) + 1),
            ),
        )
        feedback = ExperimentLedgerReader(self._store.path).strategy_research_feedback(hypothesis.agent_id)
        if not feedback or feedback[-1] != review.terminal:
            raise StrategyResearchLedgerError("sanitized_feedback_missing")
        return ScienceCycleResult(
            source_ids=tuple(item.source_id for item in hypothesis.source_refs),
            owner_agent_id=hypothesis.agent_id,
            hypothesis_id=hypothesis.hypothesis_id,
            protocol_id=protocol.protocol_id,
            attempt_ids=tuple(item.attempt_id for item in search.attempts),
            selected_attempt_id=search.selected.attempt_id,
            holdout_reveal_id=review.reveal_id,
            terminal=review.terminal,
            feedback_result_id=feedback[-1].result_id,
        )


def _preregistered_hypothesis(draft: ImmutableHypothesis) -> ImmutableHypothesis:
    methodology = strategy_research_methodology(draft.agent_id)
    gate = (
        f"fixed-seed {methodology.resampling_method.value}; repetitions={_BOOTSTRAP_REPETITIONS}; "
        f"familywise_alpha={_FAMILYWISE_ALPHA}; adjustment=bonferroni_by_max_attempts; "
        f"minimum={draft.minimum_observations}; max_width={_MAXIMUM_INTERVAL_WIDTH}"
    )
    return ImmutableHypothesis.model_validate(
        draft.model_dump(mode="python") | {"status": HypothesisStatus.PREREGISTERED, "power_or_ci_gate": gate}
    )


def _protocol(hypothesis: ImmutableHypothesis) -> FrozenScienceProtocol:
    methodology = strategy_research_methodology(hypothesis.agent_id)
    split_sha = _sha(
        hypothesis.train_period.model_dump_json()
        + hypothesis.validation_period.model_dump_json()
        + hypothesis.holdout_period_sealed_ref.content_sha256
    )
    seed = int(hypothesis.content_sha256[:16], 16)
    protocol_id = _sha(
        hypothesis.content_sha256
        + split_sha
        + str(
            (
                _BOOTSTRAP_REPETITIONS,
                seed,
                _FAMILYWISE_ALPHA,
                hypothesis.max_attempts,
                methodology.resampling_method,
            )
        )
    )
    return FrozenScienceProtocol(
        protocol_id=protocol_id,
        hypothesis_sha256=hypothesis.content_sha256,
        primary_metric=hypothesis.primary_metric,
        baseline_id=hypothesis.baseline_id,
        cost_model_id=hypothesis.cost_model_id,
        split_sha256=split_sha,
        falsification_rule=hypothesis.falsification_rule,
        max_parameter_combinations=hypothesis.search_budget.max_parameter_combinations,
        max_attempts=hypothesis.max_attempts,
        max_cpu_seconds=hypothesis.search_budget.max_cpu_seconds,
        minimum_observations=hypothesis.minimum_observations,
        maximum_interval_width=_MAXIMUM_INTERVAL_WIDTH,
        bootstrap_repetitions=_BOOTSTRAP_REPETITIONS,
        bootstrap_seed=seed,
        familywise_alpha=_FAMILYWISE_ALPHA,
        adjustment_tests=hypothesis.max_attempts,
        resampling_method=methodology.resampling_method,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = (
    "BarOutcome",
    "CompletedBarRange",
    "ScienceKernel",
    "StopTargetThresholds",
    "resolve_same_bar_outcome",
    "validate_market_time_series_online_claim",
)
