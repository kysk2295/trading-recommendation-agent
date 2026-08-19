from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.strategy_research_contract_fixtures import NOW, hypothesis
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.strategy_research_experiment_models import (
    AttemptSpec,
    ParameterValue,
    ScienceCycleResult,
    ScienceExperiment,
)
from trading_agent.strategy_research_holdout_reviewer import (
    HoldoutBranch,
    SealedHoldoutPayload,
    SealedHoldoutReviewer,
)
from trading_agent.strategy_research_ledger import StrategyResearchLedgerError
from trading_agent.strategy_research_models import ImmutableHypothesis
from trading_agent.strategy_research_science_kernel import (
    BarOutcome,
    CompletedBarRange,
    ScienceKernel,
    StopTargetThresholds,
    resolve_same_bar_outcome,
)
from trading_agent.strategy_research_types import AttemptStatus, ExpectedDirection, TerminalOutcome


def _attempt(
    value: float,
    validation: float,
) -> AttemptSpec:
    # Given: one immutable parameter branch with deterministic train/validation samples.
    return AttemptSpec(
        parameter_values=(ParameterValue(name="surprise_z", value=value),),
        status=AttemptStatus.SUCCEEDED,
        train_values=(validation / 2,) * 4,
        validation_values=(validation,) * 4,
        error_class=None,
        elapsed_cpu_seconds=1,
    )


def _terminal_attempt(value: float, status: AttemptStatus) -> AttemptSpec:
    return AttemptSpec(
        parameter_values=(ParameterValue(name="surprise_z", value=value),),
        status=status,
        train_values=(),
        validation_values=(),
        error_class=f"{status.value}_fixture",
        elapsed_cpu_seconds=1,
    )


def _cpu_attempt(value: float, validation: float, cpu: int) -> AttemptSpec:
    return _attempt(value, validation).model_copy(update={"elapsed_cpu_seconds": cpu})


def _payload(*branches: tuple[float, float], observations: int = 24) -> SealedHoldoutPayload:
    return SealedHoldoutPayload(
        reviewer_id="independent-reviewer-v1",
        branches=tuple(
            HoldoutBranch(
                parameter_values=(ParameterValue(name="surprise_z", value=parameter),),
                values=(holdout,) * observations,
                cluster_keys=tuple(f"event-{index // 2}" for index in range(observations)),
            )
            for parameter, holdout in branches
        ),
    )


def _draft(payload: SealedHoldoutPayload, *, direction: ExpectedDirection = ExpectedDirection.POSITIVE):
    seal = hypothesis().holdout_period_sealed_ref.model_copy(update={"commitment_sha256": payload.content_sha256})
    return hypothesis().model_copy(
        update={
            "expected_direction": direction,
            "holdout_period_sealed_ref": seal,
            "minimum_observations": 20,
        }
    )


def _experiment(*attempts: AttemptSpec) -> ScienceExperiment:
    return ScienceExperiment(started_at=NOW + dt.timedelta(minutes=2), attempts=attempts)


@dataclass(frozen=True, slots=True)
class _RunCase:
    draft: ImmutableHypothesis
    experiment: ScienceExperiment
    payload: SealedHoldoutPayload


def _run(tmp_path: Path, case: _RunCase):
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    return ScienceKernel(store, SealedHoldoutReviewer.from_payload(case.payload)).run(case.draft, case.experiment)


def test_validation_mutation_changes_selected_pre_holdout_branch(tmp_path: Path) -> None:
    # Given: two eligible branches and a sealed payload for both combinations.
    payload = _payload((1.0, 0.03), (1.5, -0.03))
    draft = _draft(payload)

    # When: only branch one's validation values are improved in an independent replay.
    first = _run(
        tmp_path / "first",
        _RunCase(draft, _experiment(_attempt(1.0, 0.01), _attempt(1.5, 0.02)), payload),
    )
    mutated = _run(
        tmp_path / "mutated",
        _RunCase(draft, _experiment(_attempt(1.0, 0.03), _attempt(1.5, 0.02)), payload),
    )
    tied = _run(
        tmp_path / "tied",
        _RunCase(draft, _experiment(_attempt(1.0, 0.02), _attempt(1.5, 0.02)), payload),
    )

    # Then: validation selection changes and holdout evaluates only the selected branch.
    assert first.selected_attempt_id != mutated.selected_attempt_id
    assert first.terminal.outcome is TerminalOutcome.REFUTED
    assert mutated.terminal.outcome is TerminalOutcome.SUPPORTED
    assert tied.selected_attempt_id == tied.attempt_ids[0]


def test_failed_timeout_cancel_and_censor_persist_but_are_never_selected(tmp_path: Path) -> None:
    # Given: one eligible branch and every unsuccessful terminal status.
    statuses = (
        AttemptStatus.FAILED,
        AttemptStatus.ABORTED,
        AttemptStatus.TIMED_OUT,
        AttemptStatus.CANCELLED,
        AttemptStatus.CENSORED,
    )
    payload = _payload((1.0, 0.03))
    attempts = (_attempt(1.0, 0.02), *(_terminal_attempt(1.5, status) for status in statuses))
    parameter = (
        hypothesis()
        .free_parameters[0]
        .model_copy(update={"candidate_values": (1.0, 1.5, 2.0, 2.5, 3.0, 3.5), "upper_bound": 3.5})
    )
    budget = hypothesis().search_budget.model_copy(update={"max_parameter_combinations": 6, "max_attempts": 6})
    draft = _draft(payload).model_copy(
        update={"free_parameters": (parameter,), "search_budget": budget, "max_attempts": 6}
    )

    # When: the bounded search and sealed review complete.
    result = _run(tmp_path, _RunCase(draft, _experiment(*attempts), payload))

    # Then: every submitted outcome persists while selection remains on the eligible branch.
    stored = ExperimentLedgerReader(tmp_path / "ledger.sqlite3").strategy_research_attempts(result.hypothesis_id)
    assert tuple(item.status for item in stored) == (AttemptStatus.SUCCEEDED, *statuses)
    assert result.selected_attempt_id == stored[0].attempt_id


def test_all_failed_attempts_persist_before_selection_fails_closed(tmp_path: Path) -> None:
    # Given: a bounded search containing no eligible validation branch.
    payload = _payload((1.0, 0.03))
    experiment = _experiment(_terminal_attempt(1.0, AttemptStatus.FAILED))

    # When: pre-holdout selection finds no successful branch.
    with pytest.raises(StrategyResearchLedgerError, match="eligible_attempt_missing"):
        _ = _run(tmp_path, _RunCase(_draft(payload), experiment, payload))

    # Then: the failed attempt is durable and no owner terminal feedback exists.
    reader = ExperimentLedgerReader(tmp_path / "ledger.sqlite3")
    attempts = reader.strategy_research_attempts(hypothesis().hypothesis_id)
    assert tuple(item.status for item in attempts) == (AttemptStatus.FAILED,)
    assert reader.strategy_research_feedback(hypothesis().agent_id) == ()


@pytest.mark.parametrize(
    ("attempts", "reason"),
    (
        ((_attempt(1.0, 0.01), _attempt(1.5, 0.02), _attempt(2.0, 0.03)), "parameter_combination_budget_exceeded"),
        ((_attempt(1.0, 0.01), _attempt(1.0, 0.02), _attempt(1.0, 0.03)), "attempt_budget_exceeded"),
        ((_cpu_attempt(1.0, 0.01, 31), _cpu_attempt(1.5, 0.02, 30)), "attempt_cpu_budget_exceeded"),
    ),
)
def test_search_budget_rejects_before_preregistration(
    tmp_path: Path,
    attempts: tuple[AttemptSpec, ...],
    reason: str,
) -> None:
    # Given: submitted work exceeds one preregistered budget dimension.
    payload = _payload((1.0, 0.01), (1.5, 0.01), (2.0, 0.01))

    # When / Then: every public run entry fails before persistence.
    with pytest.raises(StrategyResearchLedgerError, match=reason):
        _ = _run(tmp_path, _RunCase(_draft(payload), _experiment(*attempts), payload))
    assert ExperimentLedgerReader(tmp_path / "ledger.sqlite3").strategy_research_preregistrations() == ()


def test_commitment_mismatch_blocks_reveal_and_terminal_feedback(tmp_path: Path) -> None:
    # Given: a vault payload whose canonical hash differs from the preregistered seal.
    actual = _payload((1.0, 0.03))

    # When: the reviewer reaches the commitment firewall.
    with pytest.raises(StrategyResearchLedgerError, match="holdout_commitment_mismatch"):
        _ = _run(
            tmp_path,
            _RunCase(
                hypothesis().model_copy(update={"minimum_observations": 20}),
                _experiment(_attempt(1.0, 0.02)),
                actual,
            ),
        )

    # Then: no reveal row or owner terminal feedback exists.
    with sqlite3.connect(tmp_path / "ledger.sqlite3") as connection:
        count = connection.execute("SELECT COUNT(*) FROM strategy_research_holdout_reveals").fetchone()
    assert count == (0,)
    assert ExperimentLedgerReader(tmp_path / "ledger.sqlite3").strategy_research_feedback(hypothesis().agent_id) == ()


def test_second_reveal_fails_closed(tmp_path: Path) -> None:
    # Given: one completed reveal and byte-identical replay inputs.
    payload = _payload((1.0, 0.03))
    draft = _draft(payload)
    experiment = _experiment(_attempt(1.0, 0.02))
    run_case = _RunCase(draft, experiment, payload)
    first = _run(tmp_path, run_case)

    # When / Then: V9 rejects the second reveal for the sealed lineage.
    with pytest.raises(StrategyResearchLedgerError, match="holdout_already_revealed"):
        _ = _run(tmp_path, run_case)
    assert first.holdout_reveal_id


def test_owner_models_structurally_exclude_private_holdout_data(tmp_path: Path) -> None:
    # Given: the owner-facing request/result models and one completed private review.
    payload = _payload((1.0, 0.03))
    result = _run(tmp_path, _RunCase(_draft(payload), _experiment(_attempt(1.0, 0.02)), payload))

    # When / Then: neither public schema contains a raw or exact holdout field.
    assert set(ScienceExperiment.model_fields) == {"started_at", "attempts"}
    assert "exact_metrics" not in ScienceCycleResult.model_fields
    assert "holdout_values" not in ScienceCycleResult.model_fields
    assert result.terminal.profitability_claim is False


@pytest.mark.parametrize(
    "case",
    (
        (ExpectedDirection.POSITIVE, 0.03, 24, TerminalOutcome.SUPPORTED),
        (ExpectedDirection.POSITIVE, -0.03, 24, TerminalOutcome.REFUTED),
        (ExpectedDirection.NEGATIVE, -0.03, 24, TerminalOutcome.SUPPORTED),
        (ExpectedDirection.NEGATIVE, 0.03, 24, TerminalOutcome.REFUTED),
        (ExpectedDirection.POSITIVE, 0.03, 19, TerminalOutcome.INCONCLUSIVE),
    ),
)
def test_terminal_direction_and_information_gate_are_deterministic(
    tmp_path: Path,
    case: tuple[ExpectedDirection, float, int, TerminalOutcome],
) -> None:
    # Given: a selected branch and preregistered directional information gate.
    direction, holdout, observations, expected = case
    payload = _payload((1.0, holdout), observations=observations)

    # When: the only budget-valid entry point completes review.
    result = _run(
        tmp_path,
        _RunCase(_draft(payload, direction=direction), _experiment(_attempt(1.0, 0.02)), payload),
    )

    # Then: the terminal outcome follows direction and minimum-information rules.
    assert result.terminal.outcome is expected


def test_protocol_mutation_and_same_bar_stop_first(tmp_path: Path) -> None:
    # Given: one completed immutable protocol.
    payload = _payload((1.0, 0.03))
    draft = _draft(payload)
    experiment = _experiment(_attempt(1.0, 0.02))
    _ = _run(tmp_path, _RunCase(draft, experiment, payload))

    # When / Then: mutation conflicts and a same-bar collision remains stop-first.
    with pytest.raises(StrategyResearchLedgerError, match="preregistration_conflict"):
        _ = _run(
            tmp_path,
            _RunCase(draft.model_copy(update={"primary_metric": "mutated"}), experiment, payload),
        )
    bar = CompletedBarRange(low=98.0, high=103.0)
    thresholds = StopTargetThresholds(stop=99.0, target=102.0)
    assert resolve_same_bar_outcome(bar, thresholds) is BarOutcome.STOP
