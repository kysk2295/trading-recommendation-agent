from __future__ import annotations

import datetime as dt
import inspect
import json
import sqlite3
from collections.abc import Iterator, MutableMapping
from decimal import Decimal
from pathlib import Path

import pytest

import trading_agent.day_research_ledger as day_research_ledger
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
from trading_agent.experiment_ledger_keys import (
    DayHypothesisFamilyKey,
    DayHypothesisVersionKey,
    canonical_experiment_ledger_json,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_results import ResearchAttempt
from trading_agent.strategy_research_types import AttemptStatus, ExpectedDirection


class _CountingFamilyMap(MutableMapping[str, day_research_ledger.StoredDayHypothesisFamily]):
    entries: dict[str, day_research_ledger.StoredDayHypothesisFamily]
    lookup_count: int

    def __init__(self) -> None:
        self.entries = {}
        self.lookup_count = 0

    def __getitem__(self, key: str) -> day_research_ledger.StoredDayHypothesisFamily:
        self.lookup_count += 1
        return self.entries[key]

    def __setitem__(self, key: str, value: day_research_ledger.StoredDayHypothesisFamily) -> None:
        self.entries[key] = value

    def __delitem__(self, key: str) -> None:
        del self.entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


class _CountingVersionMap(MutableMapping[str, day_research_ledger.StoredDayHypothesisVersion]):
    entries: dict[str, day_research_ledger.StoredDayHypothesisVersion]
    lookup_count: int

    def __init__(self) -> None:
        self.entries = {}
        self.lookup_count = 0

    def __getitem__(self, key: str) -> day_research_ledger.StoredDayHypothesisVersion:
        self.lookup_count += 1
        return self.entries[key]

    def __setitem__(self, key: str, value: day_research_ledger.StoredDayHypothesisVersion) -> None:
        self.entries[key] = value

    def __delitem__(self, key: str) -> None:
        del self.entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


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
    multiple_testing_family: str | None = None,
    predictor: str = "verified_catalyst_surprise",
    market_id: MarketId = MarketId.US_EQUITIES,
) -> HypothesisVersion:
    created_at = NOW + dt.timedelta(minutes=1)
    payload = {
        "hypothesis_version_id": "",
        "family_id": family.family_id,
        "parent_version_id": None,
        "market_id": market_id,
        "universe_snapshot_id": "us-equities-liquid-20260819",
        "universe_snapshot_at": NOW,
        "source_refs": ("source:catalyst",),
        "methodology_tags": ("cross_sectional",),
        "primary_evaluation_owner": "day_research",
        "evaluation_cadence": "each_completed_bar",
        "predictor": predictor,
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
        "multiple_testing_family": hypothesis().multiple_testing_family
        if multiple_testing_family is None
        else multiple_testing_family,
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


def _child_version(parent: HypothesisVersion) -> HypothesisVersion:
    created_at = parent.first_shadow_eligible_at
    payload = parent.model_dump() | {
        "hypothesis_version_id": "",
        "parent_version_id": parent.hypothesis_version_id,
        "sampling_timestamp": created_at,
        "created_at": created_at,
        "registration_completed_bar_at": created_at + dt.timedelta(minutes=1),
        "first_shadow_eligible_at": created_at + dt.timedelta(minutes=2),
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
        "multiple_testing_budget": version.search_budget.max_attempts,
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


def test_search_budget_is_aggregated_by_market_and_multiple_testing_family(
    tmp_path: Path,
) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    first = _version(family, max_attempts=2, predictor="first_predictor")
    second = _version(family, max_attempts=2, predictor="second_predictor")
    first_attempt = _attempt(0, AttemptStatus.FAILED)
    second_attempt = _attempt(1, AttemptStatus.FAILED)

    with store.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(first)
        assert writer.register_day_hypothesis_version(second)
        assert writer.append_strategy_research_attempt(first_attempt)
        assert writer.append_strategy_research_attempt(second_attempt)
        assert writer.register_day_research_attempt_binding(
            _binding(
                first_attempt,
                first,
                search_budget_debit=2,
                multiple_testing_budget=2,
            )
        )
        with pytest.raises(InvalidExperimentLedgerSourceError):
            writer.register_day_research_attempt_binding(
                _binding(
                    second_attempt,
                    second,
                    search_budget_debit=2,
                    multiple_testing_budget=2,
                )
            )


def test_multiple_testing_budget_is_independent_across_markets(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    us_version = _version(family, max_attempts=2, predictor="us_predictor")
    kr_version = _version(
        family,
        max_attempts=2,
        predictor="kr_predictor",
        market_id=MarketId.KR_EQUITIES,
    )
    us_attempt = _attempt(0, AttemptStatus.FAILED)
    kr_attempt = _attempt(1, AttemptStatus.FAILED)

    with store.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(us_version)
        assert writer.register_day_hypothesis_version(kr_version)
        assert writer.append_strategy_research_attempt(us_attempt)
        assert writer.append_strategy_research_attempt(kr_attempt)
        assert writer.register_day_research_attempt_binding(
            _binding(
                us_attempt,
                us_version,
                search_budget_debit=2,
                multiple_testing_budget=2,
            )
        )
        assert writer.register_day_research_attempt_binding(
            _binding(
                kr_attempt,
                kr_version,
                search_budget_debit=2,
                multiple_testing_budget=2,
            )
        )


def test_family_budget_serializes_concurrent_writers_before_budget_check(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    first_version = _version(family, max_attempts=1, predictor="first_predictor")
    second_version = _version(family, max_attempts=1, predictor="second_predictor")
    first, second = _attempt(0, AttemptStatus.FAILED), _attempt(1, AttemptStatus.FAILED)
    _prepared(store, (first, second), first_version)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_version(second_version)
        with pytest.raises(ExperimentLedgerWriterLeaseUnavailableError), store.writer():
            pass
        assert writer.register_day_research_attempt_binding(_binding(first, first_version))
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(_binding(second, second_version))


def _prepared(store: ExperimentLedgerStore, attempts: tuple[ResearchAttempt, ...], version: HypothesisVersion) -> None:
    with store.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(_family())
        assert writer.register_day_hypothesis_version(version)
        for attempt in attempts:
            assert writer.append_strategy_research_attempt(attempt)


def _duplicate_canonical_parent_row(connection: sqlite3.Connection, table: str) -> None:
    row = connection.execute(f"SELECT * FROM {table}").fetchone()
    connection.execute(f"DROP TABLE {table}")
    match table:
        case "strategy_research_attempts":
            connection.execute(
                "CREATE TABLE strategy_research_attempts (attempt_key TEXT,attempt_id TEXT,hypothesis_id TEXT, "
                "branch_index INTEGER,status TEXT,payload_json TEXT)"
            )
            connection.execute(
                "CREATE INDEX strategy_research_attempts_by_hypothesis "
                "ON strategy_research_attempts(hypothesis_id,branch_index)"
            )
        case "strategy_research_preregistrations":
            connection.execute(
                "CREATE TABLE strategy_research_preregistrations (registration_key TEXT,hypothesis_id TEXT, "
                "parent_hypothesis_id TEXT,search_family_id TEXT,agent_id TEXT,protocol_version TEXT,payload_json TEXT)"
            )
            connection.execute(
                "CREATE TRIGGER strategy_research_preregistrations_parent_lineage BEFORE INSERT ON "
                "strategy_research_preregistrations WHEN NEW.parent_hypothesis_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM strategy_research_preregistrations parent WHERE "
                "parent.hypothesis_id=NEW.parent_hypothesis_id AND parent.search_family_id=NEW.search_family_id) "
                "BEGIN SELECT RAISE(ABORT, 'lineage-parent-mismatch'); END"
            )
        case "strategy_research_holdout_seals":
            connection.execute(
                "CREATE TABLE strategy_research_holdout_seals "
                "(seal_id TEXT,hypothesis_id TEXT,commitment_sha256 TEXT,payload_json TEXT)"
            )
        case _:
            raise AssertionError(table)
    for action in ("update", "delete"):
        connection.execute(
            f"CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )
    values = ",".join("?" for _ in row)
    connection.execute(f"INSERT INTO {table} VALUES ({values})", row)
    connection.execute(f"INSERT INTO {table} SELECT * FROM {table}")


@pytest.mark.parametrize(
    "table",
    ("strategy_research_attempts", "strategy_research_preregistrations", "strategy_research_holdout_seals"),
)
def test_replay_and_review_reject_duplicate_canonical_parent_rows(tmp_path: Path, table: str) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    version, attempt = _version(_family()), _attempt(0, AttemptStatus.SUCCEEDED)
    _prepared(store, (attempt,), version)
    binding = _binding(attempt, version)
    with store.writer() as writer:
        assert writer.register_day_research_attempt_binding(binding)
    with sqlite3.connect(database) as connection:
        _duplicate_canonical_parent_row(connection, table)
        connection.commit()

    # When / Then
    assert store.is_initialized() is True
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.reader().day_attempts_for_review(version.market_id, version.hypothesis_version_id)
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(binding)


def test_binding_batch_audits_once_per_writer_and_replay_audits_again(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    version = _version(_family(), max_attempts=100)
    attempts = tuple(_attempt(index, AttemptStatus.SUCCEEDED) for index in range(50))
    bindings = tuple(_binding(attempt, version) for attempt in attempts)
    audit_count = 0

    def count_full_binding_audit(statement: str) -> None:
        nonlocal audit_count
        if "FROM day_research_attempt_bindings ORDER BY rowid" in " ".join(statement.split()):
            audit_count += 1

    # When
    with store.writer() as writer:
        writer._connection.set_trace_callback(count_full_binding_audit)
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(_family())
        assert writer.register_day_hypothesis_version(version)
        for attempt, binding in zip(attempts, bindings, strict=True):
            assert writer.append_strategy_research_attempt(attempt)
            assert writer.register_day_research_attempt_binding(binding)
    records = store.reader().day_attempts_for_review(version.market_id, version.hypothesis_version_id)
    with store.writer() as writer:
        writer._connection.set_trace_callback(count_full_binding_audit)
        replay = writer.register_day_research_attempt_binding(bindings[0])

    # Then
    assert len(records) == 50
    assert replay is False
    assert audit_count == 2


def test_deep_lineage_binding_batch_reuses_the_audited_version_graph(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    versions = [_version(family, max_attempts=100)]
    for _ in range(100):
        versions.append(_child_version(versions[-1]))
    target_version = versions[-1]
    attempts = tuple(_attempt(index, AttemptStatus.SUCCEEDED) for index in range(50))
    version_lookup_count = 0

    def count_target_version_lookups(statement: str) -> None:
        nonlocal version_lookup_count
        if "FROM day_hypothesis_versions WHERE hypothesis_version_id=" in " ".join(statement.split()):
            version_lookup_count += 1

    # When
    with store.writer() as writer:
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        for version in versions:
            assert writer.register_day_hypothesis_version(version)
        for attempt in attempts:
            assert writer.append_strategy_research_attempt(attempt)
        writer._connection.set_trace_callback(count_target_version_lookups)
        for index, attempt in enumerate(attempts):
            assert writer.register_day_research_attempt_binding(
                _binding(
                    attempt,
                    target_version,
                    bound_at=target_version.first_shadow_eligible_at + dt.timedelta(minutes=index + 1),
                )
            )

    # Then
    assert version_lookup_count == 0


def test_lineage_registration_after_audit_invalidates_the_writer_graph(tmp_path: Path) -> None:
    # Given
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    initial_version = _version(family)
    child_version = _child_version(initial_version)
    first_attempt = _attempt(0, AttemptStatus.SUCCEEDED)
    second_attempt = _attempt(1, AttemptStatus.SUCCEEDED)
    audit_count = 0

    def count_full_binding_audits(statement: str) -> None:
        nonlocal audit_count
        if "FROM day_research_attempt_bindings ORDER BY rowid" in " ".join(statement.split()):
            audit_count += 1

    # When
    with store.writer() as writer:
        writer._connection.set_trace_callback(count_full_binding_audits)
        assert writer.register_strategy_research(_manifest())
        assert writer.register_day_hypothesis_family(family)
        assert writer.register_day_hypothesis_version(initial_version)
        assert writer.append_strategy_research_attempt(first_attempt)
        assert writer.append_strategy_research_attempt(second_attempt)
        assert writer.register_day_research_attempt_binding(_binding(first_attempt, initial_version))
        assert writer.register_day_hypothesis_version(child_version)
        assert writer.register_day_research_attempt_binding(
            _binding(
                second_attempt,
                child_version,
                bound_at=child_version.first_shadow_eligible_at + dt.timedelta(minutes=1),
            )
        )

    # Then
    assert audit_count == 2


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


def test_fresh_binding_rejects_unrelated_persisted_binding_corruption(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    version = _version(_family())
    existing_attempt = _attempt(0, AttemptStatus.SUCCEEDED)
    fresh_attempt = _attempt(1, AttemptStatus.SUCCEEDED)
    _prepared(store, (existing_attempt, fresh_attempt), version)
    with store.writer() as writer:
        assert writer.register_day_research_attempt_binding(_binding(existing_attempt, version))
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER day_research_attempt_bindings_no_update")
        connection.execute("UPDATE day_research_attempt_bindings SET payload_json='{}'")
        connection.execute(
            "CREATE TRIGGER day_research_attempt_bindings_no_update BEFORE UPDATE ON "
            "day_research_attempt_bindings BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )
        connection.commit()

    # When / Then
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(_binding(fresh_attempt, version))


def test_stored_lineage_graph_validation_reads_parent_edges_linearly_at_ten_thousand_nodes() -> None:
    # Given
    count = 10_000
    family_seed = _family()
    family_payload = family_seed.model_dump()
    families = _CountingFamilyMap()
    parent_family_id: str | None = None
    for index in range(count):
        family_id = f"{index:064x}"
        family = HypothesisFamily.model_construct(
            None,
            **(
                family_payload
                | {
                    "family_id": family_id,
                    "parent_family_id": parent_family_id,
                    "created_at": NOW + dt.timedelta(seconds=index),
                }
            ),
        )
        families[family_id] = day_research_ledger.StoredDayHypothesisFamily(
            DayHypothesisFamilyKey("synthetic-family"), family
        )
        parent_family_id = family_id
    version_seed = _version(family_seed)
    version_payload = version_seed.model_dump()
    versions = _CountingVersionMap()
    parent_version_id: str | None = None
    version_origin = NOW + dt.timedelta(seconds=count + 1)
    for index in range(count):
        version_id = f"{count + index:064x}"
        created_at = version_origin + dt.timedelta(minutes=index * 3)
        version = HypothesisVersion.model_construct(
            None,
            **(
                version_payload
                | {
                    "hypothesis_version_id": version_id,
                    "family_id": parent_family_id,
                    "parent_version_id": parent_version_id,
                    "sampling_timestamp": created_at,
                    "created_at": created_at,
                    "registration_completed_bar_at": created_at + dt.timedelta(minutes=1),
                    "first_shadow_eligible_at": created_at + dt.timedelta(minutes=2),
                }
            ),
        )
        versions[version_id] = day_research_ledger.StoredDayHypothesisVersion(
            DayHypothesisVersionKey("synthetic-version"), version
        )
        parent_version_id = version_id

    # When
    day_research_ledger._require_stored_family_graph(families)
    day_research_ledger._require_stored_version_graph(families, versions)

    # Then
    assert families.lookup_count <= count * 3
    assert versions.lookup_count <= count * 2


def test_module_binding_registration_does_not_expose_audit_bypass() -> None:
    # Given
    parameters = inspect.signature(day_research_ledger.register_day_research_attempt_binding).parameters

    # When / Then
    assert "stored_bindings_audited" not in parameters


def test_linear_graph_validators_reject_missing_parents_and_cycles() -> None:
    # Given
    family = _family()
    version = _version(family)
    stored_families = {
        family.family_id: day_research_ledger.StoredDayHypothesisFamily(DayHypothesisFamilyKey("family"), family)
    }
    missing_parent_version = HypothesisVersion.model_construct(
        None,
        **(version.model_dump() | {"parent_version_id": "f" * 64}),
    )
    stored_versions = {
        missing_parent_version.hypothesis_version_id: day_research_ledger.StoredDayHypothesisVersion(
            DayHypothesisVersionKey("version"), missing_parent_version
        )
    }

    # When / Then
    with pytest.raises(day_research_ledger.InvalidDayResearchLedgerSourceError):
        day_research_ledger._require_stored_version_graph(stored_families, stored_versions)
    with pytest.raises(day_research_ledger.InvalidDayResearchLedgerSourceError):
        day_research_ledger._require_acyclic_parent_graph(
            {"family-a": "family-b", "family-b": "family-a"},
            "stored_day_family_lineage_invalid",
        )


@pytest.mark.parametrize("debit", (0, -1))
def test_binding_rejects_nonpositive_budget_debit(debit: int) -> None:
    # Given
    attempt, version = _attempt(0, AttemptStatus.SUCCEEDED), _version(_family())

    # When / Then
    with pytest.raises(ValueError):
        _ = _binding(attempt, version, search_budget_debit=debit)


@pytest.mark.parametrize("field", ("attempt_id", "multiple_testing_family"))
def test_binding_rejects_empty_identity_fields(field: str) -> None:
    # Given
    attempt, version = _attempt(0, AttemptStatus.SUCCEEDED), _version(_family())

    # When / Then
    with pytest.raises(ValueError):
        _ = _binding(attempt, version, **{field: ""})


@pytest.mark.parametrize("artifact_ref", ("https://example.invalid/artifact", "artifact://unsafe/" + SHA_A))
def test_binding_rejects_unsafe_artifact_reference(artifact_ref: str) -> None:
    # Given
    attempt, version = _attempt(0, AttemptStatus.SUCCEEDED), _version(_family())

    # When / Then
    with pytest.raises(ValueError):
        _ = _binding(attempt, version, artifact_ref=artifact_ref)


@pytest.mark.parametrize("extra", ("trading_authority", "profitability_claim"))
def test_binding_rejects_authority_or_profitability_fields(extra: str) -> None:
    # Given
    binding = _binding(_attempt(0, AttemptStatus.SUCCEEDED), _version(_family()))

    # When / Then
    with pytest.raises(ValueError):
        _ = DayResearchAttemptBinding.model_validate(binding.model_dump() | {extra: False})


@pytest.mark.parametrize("milestone", ("finished", "registration"))
def test_binding_rejects_terminal_and_registration_timestamp_equality(tmp_path: Path, milestone: str) -> None:
    # Given
    family = _family()
    version = _version(family)
    attempt = _attempt(0, AttemptStatus.SUCCEEDED)
    if milestone == "registration":
        attempt = attempt.model_copy(
            update={
                "started_at": NOW,
                "finished_at": version.registration_completed_bar_at - dt.timedelta(seconds=1),
            }
        )
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    _prepared(store, (attempt,), version)
    bound_at = attempt.finished_at if milestone == "finished" else version.registration_completed_bar_at

    # When / Then
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(_binding(attempt, version, bound_at=bound_at))


@pytest.mark.parametrize("tamper", ("missing", "index", "payload", "commitment"))
def test_binding_rejects_tampered_holdout_seal(tmp_path: Path, tamper: str) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    version, attempt = _version(_family()), _attempt(0, AttemptStatus.SUCCEEDED)
    _prepared(store, (attempt,), version)
    with sqlite3.connect(database) as connection:
        action = "delete" if tamper == "missing" else "update"
        connection.execute(f"DROP TRIGGER strategy_research_holdout_seals_no_{action}")
        if tamper == "missing":
            connection.execute("DELETE FROM strategy_research_holdout_seals")
        else:
            column, value = {
                "index": ("seal_id", "other-seal"),
                "payload": ("payload_json", "{}"),
                "commitment": ("commitment_sha256", SHA_A),
            }[tamper]
            connection.execute(f"UPDATE strategy_research_holdout_seals SET {column}=?", (value,))
        connection.execute(
            f"CREATE TRIGGER strategy_research_holdout_seals_no_{action} BEFORE {action.upper()} "
            "ON strategy_research_holdout_seals BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )
        connection.commit()

    # When / Then
    assert store.is_initialized() is True
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(_binding(attempt, version))


def test_replay_and_review_reject_duplicate_attempt_rows_and_over_budget_rows(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    version, attempt = _version(_family()), _attempt(0, AttemptStatus.SUCCEEDED)
    _prepared(store, (attempt,), version)
    binding = _binding(attempt, version)
    duplicate = _binding(
        attempt,
        version,
        bound_at=binding.bound_at + dt.timedelta(seconds=1),
        search_budget_debit=2,
    )
    with store.writer() as writer:
        assert writer.register_day_research_attempt_binding(binding)
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT * FROM day_research_attempt_bindings").fetchone()
        connection.execute("DROP TABLE day_research_attempt_bindings")
        connection.execute(
            "CREATE TABLE day_research_attempt_bindings (binding_id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL, "
            "hypothesis_version_id TEXT NOT NULL,market_id TEXT NOT NULL,artifact_ref TEXT NOT NULL, "
            "multiple_testing_family TEXT NOT NULL,search_budget_debit INTEGER NOT NULL,bound_at TEXT NOT NULL, "
            "payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE INDEX day_attempt_bindings_by_version_market "
            "ON day_research_attempt_bindings(hypothesis_version_id,market_id,bound_at)"
        )
        connection.execute("CREATE TRIGGER day_research_attempt_bindings_no_update BEFORE UPDATE ON "
            "day_research_attempt_bindings BEGIN SELECT RAISE(ABORT, 'append-only'); END")
        connection.execute("CREATE TRIGGER day_research_attempt_bindings_no_delete BEFORE DELETE ON "
            "day_research_attempt_bindings BEGIN SELECT RAISE(ABORT, 'append-only'); END")
        connection.execute("INSERT INTO day_research_attempt_bindings VALUES (?,?,?,?,?,?,?,?,?)", row)
        connection.execute(
            "INSERT INTO day_research_attempt_bindings VALUES (?,?,?,?,?,?,?,?,?)",
            (
                duplicate.binding_id,
                duplicate.attempt_id,
                duplicate.hypothesis_version_id,
                duplicate.market_id.value,
                duplicate.artifact_ref,
                duplicate.multiple_testing_family,
                duplicate.search_budget_debit,
                duplicate.bound_at.isoformat(),
                canonical_experiment_ledger_json(duplicate),
            ),
        )
        connection.commit()

    # When / Then
    assert store.is_initialized() is True
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.reader().day_attempts_for_review(version.market_id, version.hypothesis_version_id)
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(binding)


def test_replay_and_review_reject_canonical_over_budget_row(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    family = _family()
    version = _version(family, max_attempts=1)
    first, second = _attempt(0, AttemptStatus.SUCCEEDED), _attempt(1, AttemptStatus.SUCCEEDED)
    _prepared(store, (first, second), version)
    binding, overflow = _binding(first, version), _binding(second, version)
    with store.writer() as writer:
        assert writer.register_day_research_attempt_binding(binding)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO day_research_attempt_bindings VALUES (?,?,?,?,?,?,?,?,?)",
            (
                overflow.binding_id,
                overflow.attempt_id,
                overflow.hypothesis_version_id,
                overflow.market_id.value,
                overflow.artifact_ref,
                overflow.multiple_testing_family,
                overflow.search_budget_debit,
                overflow.bound_at.isoformat(),
                canonical_experiment_ledger_json(overflow),
            ),
        )
        connection.commit()

    # When / Then
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.reader().day_attempts_for_review(version.market_id, version.hypothesis_version_id)
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(binding)


def test_manifest_family_and_kind_neutral_safe_artifact_preserve_attempt_bytes(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    family = _family()
    incompatible = _version(family, multiple_testing_family="other-family")
    attempt = _attempt(0, AttemptStatus.SUCCEEDED).model_copy(
        update={"artifact_refs": (f"artifact://safe/{SHA_B}",)}
    )
    _prepared(store, (attempt,), incompatible)
    with sqlite3.connect(database) as connection:
        before: tuple[str] = connection.execute("SELECT payload_json FROM strategy_research_attempts").fetchone()

    # When / Then
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_research_attempt_binding(_binding(attempt, incompatible, artifact_ref=f"artifact://safe/{SHA_B}"))
    compatible = _version(family)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_version(compatible)
        assert writer.register_day_research_attempt_binding(_binding(attempt, compatible, artifact_ref=f"artifact://safe/{SHA_B}"))
    with sqlite3.connect(database) as connection:
        after: tuple[str] = connection.execute("SELECT payload_json FROM strategy_research_attempts").fetchone()
    assert after == before


def test_reader_filters_deterministically_and_never_creates_or_mutates(tmp_path: Path) -> None:
    # Given
    missing = ExperimentLedgerStore(tmp_path / "missing.sqlite3")
    family = _family()
    first_version = _version(family)
    second_version = _version(family, predictor="other_predictor")
    first = _attempt(0, AttemptStatus.SUCCEEDED)
    second = _attempt(1, AttemptStatus.FAILED)
    third = _attempt(2, AttemptStatus.ABORTED)
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    _prepared(store, (first, second, third), first_version)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_version(second_version)
        assert writer.register_day_research_attempt_binding(_binding(second, second_version))
        assert writer.register_day_research_attempt_binding(_binding(third, first_version))
        assert writer.register_day_research_attempt_binding(_binding(first, first_version))

    # When
    first_records = store.reader().day_attempts_for_review(
        first_version.market_id,
        first_version.hypothesis_version_id,
    )
    second_records = store.reader().day_attempts_for_review(
        second_version.market_id,
        second_version.hypothesis_version_id,
    )

    # Then
    assert missing.reader().day_attempts_for_review(MarketId.US_EQUITIES, first_version.hypothesis_version_id) == ()
    assert not missing.path.exists()
    assert tuple(record.attempt.attempt_id for record in first_records) == (first.attempt_id, third.attempt_id)
    assert tuple(record.attempt.attempt_id for record in second_records) == (second.attempt_id,)
    with (
        store.reader()._reader_connection() as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute("INSERT INTO day_research_attempt_bindings DEFAULT VALUES")
