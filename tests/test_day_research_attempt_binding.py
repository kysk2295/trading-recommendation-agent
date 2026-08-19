from __future__ import annotations

import datetime as dt
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from tests.strategy_research_contract_fixtures import NOW, SHA_A, SHA_B, hypothesis
from trading_agent.day_hypothesis_models import (
    CostModelDeclaration,
    FreeParameter,
    HypothesisFamily,
    HypothesisVersion,
    SearchBudget,
    TargetHorizon,
)
from trading_agent.day_research_attempt_binding import DayResearchAttemptBinding
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_results import ResearchAttempt
from trading_agent.strategy_research_types import AttemptStatus, ExpectedDirection


def _family() -> HypothesisFamily:
    payload = {
        "family_id": "",
        "parent_family_id": None,
        "canonical_question": "Does verified catalyst surprise persist after a completed bar?",
        "economic_mechanism": "Prices incorporate verified surprise with bounded delay.",
        "alternative_explanations": ("market beta",),
        "counterfactual_baseline": "market-adjusted zero-return baseline",
        "created_by": "day_research",
        "created_at": NOW,
        "source_lineage": ("research:catalyst",),
    }
    return HypothesisFamily.model_validate(payload | {"family_id": HypothesisFamily.canonical_id_for(payload)})


def _version(
    family: HypothesisFamily,
    *,
    code_sha256: str = SHA_A,
    data_manifest_sha256: str = SHA_B,
    max_attempts: int = 6,
) -> HypothesisVersion:
    created_at = NOW + dt.timedelta(minutes=1)
    payload = {
        "hypothesis_version_id": "",
        "family_id": family.family_id,
        "parent_version_id": None,
        "market_id": MarketId.US_EQUITIES,
        "universe_snapshot_id": "us-equities-liquid-20260819",
        "universe_snapshot_at": NOW,
        "source_refs": ("source:catalyst",),
        "methodology_tags": ("cross_sectional",),
        "primary_evaluation_owner": "day_research",
        "evaluation_cadence": "each_completed_bar",
        "predictor": "verified_catalyst_surprise",
        "sampling_timestamp": created_at,
        "target": "next_completed_bar_return",
        "target_horizon": TargetHorizon(duration=dt.timedelta(minutes=5)),
        "expected_direction": ExpectedDirection.POSITIVE,
        "entry_rule": "enter_next_completed_bar",
        "exit_rule": "exit_at_target_horizon",
        "stop_rule": "exit_at_preregistered_stop",
        "invalidation_rule": "invalidate_when_spread_missing",
        "threshold": Decimal("1"),
        "cost_model": CostModelDeclaration(
            model_id="us_equities_cost_v1", commission_bps=Decimal("1"), slippage_bps=Decimal("2")
        ),
        "free_parameters": (FreeParameter(name="surprise", values=(Decimal("1"),)),),
        "search_budget": SearchBudget(
            max_parameter_combinations=max_attempts, max_attempts=max_attempts, max_cpu_seconds=60
        ),
        "multiple_testing_family": hypothesis().multiple_testing_family,
        "model_sha256": SHA_A,
        "prompt_sha256": SHA_A,
        "code_sha256": code_sha256,
        "data_manifest_sha256": data_manifest_sha256,
        "protocol_sha256": SHA_A,
        "created_at": created_at,
        "registration_completed_bar_at": created_at + dt.timedelta(minutes=1),
        "first_shadow_eligible_at": created_at + dt.timedelta(minutes=2),
        "trading_authority": False,
        "profitability_claim": False,
    }
    return HypothesisVersion.model_validate(
        payload | {"hypothesis_version_id": HypothesisVersion.canonical_id_for(payload)}
    )


def _attempt(index: int, status: AttemptStatus) -> ResearchAttempt:
    finished_at = NOW + dt.timedelta(minutes=4 + index)
    return ResearchAttempt(
        attempt_id=f"attempt-{index}",
        hypothesis_id=hypothesis().hypothesis_id,
        branch_index=index,
        input_hashes=(SHA_A,),
        code_sha256=SHA_A,
        data_manifest_sha256=SHA_B,
        started_at=finished_at - dt.timedelta(minutes=1),
        finished_at=finished_at,
        status=status,
        artifact_refs=(f"artifact://safe/{SHA_A}",) if status is AttemptStatus.SUCCEEDED else (),
        error_class=None if status is AttemptStatus.SUCCEEDED else f"{status.value}_error",
        max_cpu_seconds=60,
    )


def _binding(
    attempt: ResearchAttempt,
    version: HypothesisVersion,
    **overrides: object,
) -> DayResearchAttemptBinding:
    payload = {
        "binding_id": "",
        "attempt_id": attempt.attempt_id,
        "market_id": version.market_id,
        "hypothesis_version_id": version.hypothesis_version_id,
        "artifact_ref": f"artifact://safe/{SHA_A}",
        "multiple_testing_family": version.multiple_testing_family,
        "search_budget_debit": 1,
        "bound_at": (attempt.finished_at or version.first_shadow_eligible_at) + dt.timedelta(minutes=1),
    }
    payload.update(overrides)
    return DayResearchAttemptBinding.model_validate(
        payload | {"binding_id": DayResearchAttemptBinding.canonical_id_for(payload)}
    )


def _manifest() -> PreregistrationManifest:
    return PreregistrationManifest.from_hypothesis(hypothesis(), preregistered_at=NOW)


def test_registers_and_reads_every_terminal_attempt_status(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    version = _version(family)
    attempts = tuple(_attempt(index, status) for index, status in enumerate(tuple(AttemptStatus)[1:]))

    # When
    with store.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(version)
        for attempt in attempts:
            assert writer.append_strategy_research_attempt(attempt)
            assert writer.register_day_research_attempt_binding(_binding(attempt, version))
    records = store.reader().day_attempts_for_review(version.market_id, version.hypothesis_version_id)

    # Then
    assert tuple(record.attempt.status for record in records) == tuple(AttemptStatus)[1:]


def _prepared(store: ExperimentLedgerStore, attempts: tuple[ResearchAttempt, ...], version: HypothesisVersion) -> None:
    with store.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(_family())
        assert writer.register_day_hypothesis_version(version)
        for attempt in attempts:
            assert writer.append_strategy_research_attempt(attempt)


def test_binding_is_exactly_once_and_attempt_cannot_rebind(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family, attempt = _family(), _attempt(0, AttemptStatus.SUCCEEDED)
    version = _version(family)
    _prepared(store, (attempt,), version)
    binding = _binding(attempt, version)

    # When
    with store.writer() as writer:
        first = writer.register_day_research_attempt_binding(binding)
        replay = writer.register_day_research_attempt_binding(binding)

    # Then
    assert (first, replay) == (True, False)
    with pytest.raises(ExperimentLedgerConflictError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(_binding(attempt, version, artifact_ref=f"artifact://safe/{SHA_B}"))
    changed_same_id = DayResearchAttemptBinding.model_construct(
        None,
        **(binding.model_dump() | {"artifact_ref": f"artifact://safe/{SHA_B}"})
    )
    with pytest.raises(ExperimentLedgerConflictError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(changed_same_id)


@pytest.mark.parametrize(
    "case",
    (
        "missing_attempt",
        "missing_version",
        "started",
        "cross_market",
        "family",
        "artifact",
        "success_artifact",
        "time",
        "version_time",
    ),
)
def test_public_writer_rejects_invalid_binding_sources(tmp_path: Path, case: str) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    version = _version(family)
    status = AttemptStatus.STARTED if case == "started" else AttemptStatus.SUCCEEDED
    attempt = _attempt(0, status) if status is not AttemptStatus.STARTED else ResearchAttempt(
        attempt_id="attempt-0", hypothesis_id=hypothesis().hypothesis_id, branch_index=0, input_hashes=(SHA_A,),
        code_sha256=SHA_A, data_manifest_sha256=SHA_B, started_at=NOW + dt.timedelta(minutes=3), finished_at=None,
        status=status, artifact_refs=(), error_class=None, max_cpu_seconds=60,
    )
    _prepared(store, (attempt,), version)
    overrides: dict[str, object] = {
        "missing_attempt": {"attempt_id": "missing-attempt"},
        "missing_version": {"hypothesis_version_id": "0" * 64},
        "cross_market": {"market_id": MarketId.KR_EQUITIES},
        "family": {"multiple_testing_family": "wrong-family"},
        "artifact": {"artifact_ref": f"artifact://safe/{SHA_B}"},
        "success_artifact": {"artifact_ref": f"artifact://safe/{SHA_B}"},
        "time": {"bound_at": NOW + dt.timedelta(minutes=3)},
        "version_time": {"bound_at": NOW + dt.timedelta(minutes=1)},
    }.get(case, {})
    if case == "artifact":
        failed = _attempt(0, AttemptStatus.FAILED)
        store = ExperimentLedgerStore(tmp_path / "failed.sqlite3")
        _prepared(store, (failed,), version)
        attempt = failed

    # When / Then
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(_binding(attempt, version, **overrides))
    assert store.reader().day_attempts_for_review(version.market_id, version.hypothesis_version_id) == ()


def test_code_data_and_cumulative_budget_are_enforced(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    incompatible = _version(family, code_sha256=SHA_B)
    attempt = _attempt(0, AttemptStatus.SUCCEEDED)
    _prepared(store, (attempt,), incompatible)

    # When / Then
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(_binding(attempt, incompatible))
    incompatible_data = _version(family, data_manifest_sha256=SHA_A)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_version(incompatible_data)
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.register_day_research_attempt_binding(_binding(attempt, incompatible_data))

    bounded = _version(family, max_attempts=1)
    first, second = _attempt(0, AttemptStatus.SUCCEEDED), _attempt(1, AttemptStatus.SUCCEEDED)
    budget_store = ExperimentLedgerStore(tmp_path / "budget.sqlite3")
    _prepared(budget_store, (first, second), bounded)
    with budget_store.writer() as writer:
        assert writer.register_day_research_attempt_binding(_binding(first, bounded))
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.register_day_research_attempt_binding(_binding(second, bounded))
    assert len(budget_store.reader().day_attempts_for_review(bounded.market_id, bounded.hypothesis_version_id)) == 1


@pytest.mark.parametrize("tamper", ("binding_index", "binding_payload", "attempt_parent"))
def test_forged_id_naive_time_and_tampered_rows_fail_closed(tmp_path: Path, tamper: str) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    version, attempt = _version(_family()), _attempt(0, AttemptStatus.SUCCEEDED)
    _prepared(store, (attempt,), version)
    binding = _binding(attempt, version)
    forged = DayResearchAttemptBinding.model_construct(
        None,
        **(binding.model_dump() | {"binding_id": "0" * 64}),
    )

    # When / Then
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(forged)
    with pytest.raises(ValueError):
        _ = DayResearchAttemptBinding.model_validate(
            binding.model_dump() | {"bound_at": binding.bound_at.replace(tzinfo=None)}
        )
    with pytest.raises(ValueError):
        _ = binding.model_copy(update={"binding_id": "0" * 64})
    with store.writer() as writer:
        assert writer.register_day_research_attempt_binding(binding)
    with sqlite3.connect(database) as connection:
        table = "strategy_research_attempts" if tamper == "attempt_parent" else "day_research_attempt_bindings"
        trigger = f"{table}_no_update"
        column = "payload_json" if tamper != "binding_index" else "artifact_ref"
        if tamper == "binding_index":
            value = f"artifact://safe/{SHA_B}"
        elif tamper == "binding_payload":
            raw: tuple[str] = connection.execute(f"SELECT payload_json FROM {table}").fetchone()
            value = json.dumps(json.loads(raw[0]), indent=2)
        else:
            value = "{}"
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(f"UPDATE {table} SET {column}=?", (value,))
        connection.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )
        connection.commit()
    assert store.is_initialized() is True
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.reader().day_attempts_for_review(version.market_id, version.hypothesis_version_id)


@pytest.mark.parametrize("debit", (0, -1))
def test_binding_rejects_nonpositive_budget_debit(debit: int) -> None:
    # Given
    attempt, version = _attempt(0, AttemptStatus.SUCCEEDED), _version(_family())

    # When / Then
    with pytest.raises(ValueError):
        _ = _binding(attempt, version, search_budget_debit=debit)
