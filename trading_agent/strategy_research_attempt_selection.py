from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import assert_never

from trading_agent.strategy_research_experiment_models import (
    AttemptSpec,
    FrozenScienceProtocol,
    ParameterValue,
    ScienceExperiment,
)
from trading_agent.strategy_research_ledger import StrategyResearchLedgerError
from trading_agent.strategy_research_models import ImmutableHypothesis
from trading_agent.strategy_research_results import ResearchAttempt
from trading_agent.strategy_research_types import AttemptStatus, ExpectedDirection


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    attempt_id: str
    branch_index: int
    parameter_values: tuple[ParameterValue, ...]
    train_mean: float
    validation_mean: float


@dataclass(frozen=True, slots=True)
class PreparedSearch:
    attempts: tuple[ResearchAttempt, ...]
    selected: AttemptSummary | None


@dataclass(frozen=True, slots=True)
class _AttemptBuildContext:
    hypothesis: ImmutableHypothesis
    protocol: FrozenScienceProtocol
    cycle_started_at: dt.datetime


def prepare_search(
    hypothesis: ImmutableHypothesis,
    protocol: FrozenScienceProtocol,
    experiment: ScienceExperiment,
) -> PreparedSearch:
    _require_budget(protocol, experiment)
    _require_parameter_space(hypothesis, experiment)
    persisted: list[ResearchAttempt] = []
    eligible: list[AttemptSummary] = []
    context = _AttemptBuildContext(hypothesis, protocol, experiment.started_at)
    for branch_index, spec in enumerate(experiment.attempts):
        attempt = _persisted_attempt(context, branch_index, spec)
        persisted.append(attempt)
        match spec.status:
            case AttemptStatus.SUCCEEDED:
                eligible.append(
                    AttemptSummary(
                        attempt_id=attempt.attempt_id,
                        branch_index=branch_index,
                        parameter_values=spec.parameter_values,
                        train_mean=sum(spec.train_values) / len(spec.train_values),
                        validation_mean=sum(spec.validation_values) / len(spec.validation_values),
                    )
                )
            case (
                AttemptStatus.FAILED
                | AttemptStatus.ABORTED
                | AttemptStatus.TIMED_OUT
                | AttemptStatus.CANCELLED
                | AttemptStatus.CENSORED
            ):
                pass
            case AttemptStatus.STARTED:
                raise StrategyResearchLedgerError("started_attempt_invalid")
            case unreachable:
                assert_never(unreachable)
    selected = (
        max(eligible, key=lambda item: (_selection_score(hypothesis.expected_direction, item), -item.branch_index))
        if eligible
        else None
    )
    return PreparedSearch(tuple(persisted), selected)


def _require_budget(protocol: FrozenScienceProtocol, experiment: ScienceExperiment) -> None:
    combinations = {tuple((item.name, item.value) for item in spec.parameter_values) for spec in experiment.attempts}
    if len(combinations) > protocol.max_parameter_combinations:
        raise StrategyResearchLedgerError("parameter_combination_budget_exceeded")
    if len(experiment.attempts) > protocol.max_attempts:
        raise StrategyResearchLedgerError("attempt_budget_exceeded")
    if sum(item.elapsed_cpu_seconds for item in experiment.attempts) > protocol.max_cpu_seconds:
        raise StrategyResearchLedgerError("attempt_cpu_budget_exceeded")


def _require_parameter_space(hypothesis: ImmutableHypothesis, experiment: ScienceExperiment) -> None:
    allowed = {item.name: frozenset(item.candidate_values) for item in hypothesis.free_parameters}
    required_names = frozenset(allowed)
    for spec in experiment.attempts:
        supplied = frozenset(item.name for item in spec.parameter_values)
        if supplied != required_names or any(item.value not in allowed[item.name] for item in spec.parameter_values):
            raise StrategyResearchLedgerError("attempt_parameter_invalid")


def _persisted_attempt(
    context: _AttemptBuildContext,
    branch_index: int,
    spec: AttemptSpec,
) -> ResearchAttempt:
    branch_started = context.cycle_started_at + dt.timedelta(seconds=branch_index)
    input_hash = spec.content_sha256
    artifact_hash = _sha(f"{context.protocol.protocol_id}:{input_hash}:train-validation-summary")
    return ResearchAttempt(
        attempt_id=f"attempt-{_sha(f'{context.protocol.protocol_id}:{input_hash}:{branch_index}')}",
        hypothesis_id=context.hypothesis.hypothesis_id,
        branch_index=branch_index,
        input_hashes=(context.protocol.protocol_id, input_hash),
        code_sha256=context.hypothesis.code_sha256,
        data_manifest_sha256=context.hypothesis.data_manifest_sha256,
        started_at=branch_started,
        finished_at=branch_started + dt.timedelta(seconds=spec.elapsed_cpu_seconds),
        status=spec.status,
        artifact_refs=(f"artifact://safe/{artifact_hash}",) if spec.status is AttemptStatus.SUCCEEDED else (),
        error_class=spec.error_class,
        max_cpu_seconds=context.protocol.max_cpu_seconds,
    )


def _selection_score(direction: ExpectedDirection, summary: AttemptSummary) -> float:
    match direction:
        case ExpectedDirection.POSITIVE:
            return summary.validation_mean
        case ExpectedDirection.NEGATIVE:
            return -summary.validation_mean
        case ExpectedDirection.TWO_SIDED:
            return abs(summary.validation_mean)
        case unreachable:
            assert_never(unreachable)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = ("AttemptSummary", "PreparedSearch", "prepare_search")
