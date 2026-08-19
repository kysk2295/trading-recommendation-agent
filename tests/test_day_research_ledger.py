from __future__ import annotations

import datetime as dt
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

import trading_agent.experiment_ledger_store as ledger_store_module
from trading_agent.day_hypothesis_models import (
    CostModelDeclaration,
    FreeParameter,
    HypothesisFamily,
    HypothesisVersion,
    SearchBudget,
    TargetHorizon,
)
from trading_agent.day_research_ledger import (
    InvalidDayResearchLedgerSourceError,
    require_same_market,
)
from trading_agent.day_research_ledger_schema import DAY_RESEARCH_SCHEMA_OBJECTS
from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
    day_hypothesis_version_key,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import ExpectedDirection

CREATED_AT = dt.datetime(2026, 8, 20, 13, 30, tzinfo=dt.UTC)


def _family(
    *,
    parent: HypothesisFamily | None = None,
    created_at: dt.datetime = CREATED_AT,
    question: str = "Does opening relative volume predict a same-session continuation?",
) -> HypothesisFamily:
    payload = {
        "family_id": "",
        "parent_family_id": None if parent is None else parent.family_id,
        "canonical_question": question,
        "economic_mechanism": "Institutional order imbalance persists after price discovery.",
        "alternative_explanations": ("market beta", "news reversal"),
        "counterfactual_baseline": "market-adjusted zero-return baseline",
        "created_by": "day_discovery",
        "created_at": created_at,
        "source_lineage": ("research:market-context", "research:opening-volume"),
    }
    family_id = HypothesisFamily.canonical_id_for(payload)
    return HypothesisFamily.model_validate(payload | {"family_id": family_id})


def _version(
    family: HypothesisFamily,
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
    parent: HypothesisVersion | None = None,
    created_at: dt.datetime = CREATED_AT + dt.timedelta(minutes=2),
    predictor: str = "relative_opening_volume",
) -> HypothesisVersion:
    payload = {
        "hypothesis_version_id": "",
        "family_id": family.family_id,
        "parent_version_id": None if parent is None else parent.hypothesis_version_id,
        "market_id": market_id,
        "universe_snapshot_id": f"{market_id.value}-liquid-universe-2026-08-20",
        "universe_snapshot_at": created_at - dt.timedelta(minutes=2),
        "source_refs": ("source:market-context", "source:opening-volume"),
        "methodology_tags": ("cross_sectional", "intraday"),
        "primary_evaluation_owner": "day_research",
        "evaluation_cadence": "each_completed_bar",
        "predictor": predictor,
        "sampling_timestamp": created_at - dt.timedelta(minutes=1),
        "target": "next_5m_market_adjusted_return",
        "target_horizon": TargetHorizon(duration=dt.timedelta(minutes=5)),
        "expected_direction": ExpectedDirection.POSITIVE,
        "entry_rule": "enter_next_completed_bar",
        "exit_rule": "exit_at_target_horizon",
        "stop_rule": "exit_when_loss_exceeds_one_r",
        "invalidation_rule": "invalidate_when_spread_missing",
        "threshold": Decimal("2"),
        "cost_model": CostModelDeclaration(
            model_id=f"{market_id.value}_cost_v1",
            commission_bps=Decimal("1"),
            slippage_bps=Decimal("2"),
        ),
        "free_parameters": (FreeParameter(name="relative_volume", values=(Decimal("1.5"), Decimal("2"))),),
        "search_budget": SearchBudget(
            max_parameter_combinations=2,
            max_attempts=2,
            max_cpu_seconds=60,
        ),
        "multiple_testing_family": "opening-volume-day-v1",
        "model_sha256": "1" * 64,
        "prompt_sha256": "2" * 64,
        "code_sha256": "3" * 64,
        "data_manifest_sha256": "4" * 64,
        "protocol_sha256": "5" * 64,
        "created_at": created_at,
        "registration_completed_bar_at": created_at + dt.timedelta(minutes=1),
        "first_shadow_eligible_at": created_at + dt.timedelta(minutes=2),
        "trading_authority": False,
        "profitability_claim": False,
    }
    version_id = HypothesisVersion.canonical_id_for(payload)
    return HypothesisVersion.model_validate(payload | {"hypothesis_version_id": version_id})


def test_fresh_schema_has_exact_day_objects(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with ExperimentLedgerStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        objects = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'day_%'").fetchall()
        )

    tables = {
        "day_hypothesis_families",
        "day_hypothesis_versions",
        "day_research_attempt_bindings",
        "day_strategy_capsules",
        "day_forward_trials",
        "day_forward_trial_events",
        "day_promotion_decisions",
        "day_execution_eligibility_events",
        "day_exploration_policies",
    }
    indexes = {
        "day_hypothesis_versions_by_family_market",
        "day_attempt_bindings_by_version_market",
        "day_capsules_by_version_market",
        "day_forward_trials_by_capsule_version_market_session",
        "day_forward_trial_events_by_trial_market_session_sequence",
        "day_promotion_decisions_by_capsule_market_session",
        "day_execution_eligibility_by_capsule_market_session",
        "day_exploration_policies_by_market_session",
    }
    triggers = {f"{table}_no_{operation}" for table in tables for operation in ("update", "delete")}
    assert objects == DAY_RESEARCH_SCHEMA_OBJECTS == tables | indexes | triggers
    assert ledger_store_module._V10_SCHEMA_OBJECTS == (
        ledger_store_module._V9_SCHEMA_OBJECTS | DAY_RESEARCH_SCHEMA_OBJECTS
    )


def test_registers_and_reads_family_and_distinct_market_versions(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    us_version = _version(family)
    kr_version = _version(family, market_id=MarketId.KR_EQUITIES)

    with store.writer() as writer:
        assert writer.register_day_hypothesis_family(family) is True
        assert writer.register_day_hypothesis_family(family) is False
        assert writer.register_day_hypothesis_version(us_version) is True
        assert writer.register_day_hypothesis_version(us_version) is False
        assert writer.register_day_hypothesis_version(kr_version) is True

    reader = store.reader()
    stored_family = reader.day_hypothesis_family(family.family_id)
    stored_version = reader.day_hypothesis_version(us_version.hypothesis_version_id)
    assert stored_family is not None and stored_family.family == family
    assert stored_version is not None and stored_version.version == us_version
    assert tuple(item.version for item in reader.day_hypothesis_versions(family.family_id)) == (
        us_version,
        kr_version,
    )


def test_cross_market_reason_is_stable() -> None:
    with pytest.raises(InvalidDayResearchLedgerSourceError) as captured:
        require_same_market(MarketId.US_EQUITIES, MarketId.KR_EQUITIES)

    assert captured.value.reason == "day_research_cross_market_reference"


def test_schema_projects_attempt_and_shadow_eligibility_contract(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with ExperimentLedgerStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        attempt_columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(day_research_attempt_bindings)")
        )
        version_columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(day_hypothesis_versions)"))
        attempt_indexes = tuple(
            row[1] for row in connection.execute("PRAGMA index_list(day_research_attempt_bindings)")
        )

    assert attempt_columns == (
        "binding_id",
        "attempt_id",
        "hypothesis_version_id",
        "market_id",
        "artifact_ref",
        "multiple_testing_family",
        "search_budget_debit",
        "bound_at",
        "payload_json",
    )
    assert "first_shadow_eligible_at" in version_columns
    assert any(index.startswith("sqlite_autoindex_day_research_attempt_bindings") for index in attempt_indexes)


@pytest.mark.parametrize("kind", ("family", "version"))
def test_conflicting_domain_identity_is_rejected(tmp_path: Path, kind: str) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    version = _version(family)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_family(family) is True
        if kind == "version":
            assert writer.register_day_hypothesis_version(version) is True

    if kind == "family":
        conflicting = family.model_copy()
        object.__setattr__(conflicting, "canonical_question", "Changed question")
        with pytest.raises(ExperimentLedgerConflictError), store.writer() as writer:
            _ = writer.register_day_hypothesis_family(conflicting)
    else:
        conflicting = version.model_copy()
        object.__setattr__(conflicting, "predictor", "changed_predictor")
        with pytest.raises(ExperimentLedgerConflictError), store.writer() as writer:
            _ = writer.register_day_hypothesis_version(conflicting)


def test_missing_family_is_rejected_before_version_insert(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    version = _version(_family())

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_hypothesis_version(version)

    assert store.day_hypothesis_versions() == ()


def test_family_parent_must_exist_and_precede_child(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    parent = _family()
    child = _family(
        parent=parent,
        created_at=CREATED_AT + dt.timedelta(minutes=1),
        question="Does the relationship survive a volatility control?",
    )
    orphan = _family(
        parent=parent,
        created_at=CREATED_AT + dt.timedelta(minutes=2),
        question="Does the relationship survive a liquidity control?",
    )
    incoherent = _family(
        parent=parent,
        created_at=CREATED_AT - dt.timedelta(minutes=1),
        question="Does the relationship precede its parent?",
    )

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_hypothesis_family(orphan)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_family(parent) is True
        assert writer.register_day_hypothesis_family(child) is True
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_hypothesis_family(incoherent)


@pytest.mark.parametrize("failure", ("missing", "cross_family", "cross_market", "temporal"))
def test_version_parent_lineage_is_rejected_before_insert(tmp_path: Path, failure: str) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    other_family = _family(question="Does a closing imbalance predict overnight reversal?")
    parent = _version(family)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_family(family) is True
        assert writer.register_day_hypothesis_family(other_family) is True
        if failure != "missing":
            assert writer.register_day_hypothesis_version(parent) is True

    if failure == "missing":
        candidate = _version(family, parent=parent, created_at=CREATED_AT + dt.timedelta(minutes=5))
    elif failure == "cross_family":
        candidate = _version(
            other_family,
            parent=parent,
            created_at=CREATED_AT + dt.timedelta(minutes=5),
        )
    elif failure == "cross_market":
        candidate = _version(
            family,
            parent=parent,
            market_id=MarketId.KR_EQUITIES,
            created_at=CREATED_AT + dt.timedelta(minutes=5),
        )
    else:
        candidate = _version(
            family,
            parent=parent,
            created_at=parent.created_at,
            predictor="same_time_child",
        )

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_hypothesis_version(candidate)

    assert len(store.day_hypothesis_versions()) == (0 if failure == "missing" else 1)


def test_version_parent_must_be_fully_eligible_before_child_creation(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    family = _family()
    parent = _version(family)
    child = _version(
        family,
        parent=parent,
        created_at=parent.created_at + dt.timedelta(minutes=1),
        predictor="premature_child",
    )
    with store.writer() as writer:
        assert writer.register_day_hypothesis_family(family) is True
        assert writer.register_day_hypothesis_version(parent) is True

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_day_hypothesis_version(child)

    assert tuple(stored.version for stored in store.day_hypothesis_versions()) == (parent,)


def test_reader_rejects_persisted_parent_milestone_overlap(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    family = _family()
    parent = _version(family)
    child = _version(
        family,
        parent=parent,
        created_at=parent.created_at + dt.timedelta(minutes=1),
        predictor="persisted_premature_child",
    )
    with store.writer() as writer:
        assert writer.register_day_hypothesis_family(family) is True
        assert writer.register_day_hypothesis_version(parent) is True
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO day_hypothesis_versions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                day_hypothesis_version_key(child),
                child.hypothesis_version_id,
                child.family_id,
                child.parent_version_id,
                child.market_id.value,
                child.created_at.isoformat(),
                child.registration_completed_bar_at.isoformat(),
                child.first_shadow_eligible_at.isoformat(),
                canonical_experiment_ledger_json(child),
            ),
        )
        connection.commit()

    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.day_hypothesis_versions()


@pytest.mark.parametrize("kind", ("family", "version"))
@pytest.mark.parametrize("identity_case", ("missing", "wrong_type"))
def test_forged_boundary_identity_is_rejected_before_sql(
    tmp_path: Path,
    kind: str,
    identity_case: str,
) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    forged: HypothesisFamily | HypothesisVersion
    if kind == "family":
        forged = HypothesisFamily.model_construct()
        if identity_case == "wrong_type":
            object.__setattr__(forged, "family_id", 7)
        with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
            _ = writer.register_day_hypothesis_family(forged)
    else:
        forged = HypothesisVersion.model_construct()
        if identity_case == "wrong_type":
            object.__setattr__(forged, "hypothesis_version_id", 7)
        with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
            _ = writer.register_day_hypothesis_version(forged)

    assert store.day_hypothesis_families() == ()
    assert store.day_hypothesis_versions() == ()


@pytest.mark.parametrize(
    ("table", "identity_column"),
    (
        ("day_hypothesis_families", "family_id"),
        ("day_hypothesis_versions", "hypothesis_version_id"),
        ("day_research_attempt_bindings", "binding_id"),
        ("day_strategy_capsules", "capsule_id"),
        ("day_forward_trials", "trial_id"),
        ("day_forward_trial_events", "event_id"),
        ("day_promotion_decisions", "decision_id"),
        ("day_execution_eligibility_events", "eligibility_event_id"),
        ("day_exploration_policies", "policy_id"),
    ),
)
@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
def test_every_day_table_is_append_only(
    tmp_path: Path,
    table: str,
    identity_column: str,
    operation: str,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    with ExperimentLedgerStore(database).writer():
        pass
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        values = tuple(_placeholder(column) for column in columns)
        connection.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        statement = (
            f"UPDATE {table} SET payload_json=payload_json WHERE {identity_column}=?"
            if operation == "UPDATE"
            else f"DELETE FROM {table} WHERE {identity_column}=?"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(statement, (values[columns.index(identity_column)],))


@pytest.mark.parametrize("target", ("family_key", "family_index", "family_payload", "version_key", "version_index"))
def test_reads_fail_closed_on_day_tampering(tmp_path: Path, target: str) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    family = _family()
    version = _version(family)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_family(family) is True
        assert writer.register_day_hypothesis_version(version) is True

    with sqlite3.connect(database) as connection:
        if target.startswith("family"):
            connection.execute("DROP TRIGGER day_hypothesis_families_no_update")
            column = {
                "family_key": "family_key",
                "family_index": "created_at",
                "family_payload": "payload_json",
            }[target]
            value = (
                "0" * 64
                if target == "family_key"
                else "1990-01-01T00:00:00+00:00"
                if target == "family_index"
                else "{}"
            )
            connection.execute(f"UPDATE day_hypothesis_families SET {column}=?", (value,))
            _restore_update_trigger(connection, "day_hypothesis_families")
        else:
            connection.execute("DROP TRIGGER day_hypothesis_versions_no_update")
            column = "version_key" if target == "version_key" else "market_id"
            value = "0" * 64 if target == "version_key" else MarketId.KR_EQUITIES.value
            connection.execute(f"UPDATE day_hypothesis_versions SET {column}=?", (value,))
            _restore_update_trigger(connection, "day_hypothesis_versions")
        connection.commit()

    assert store.is_initialized() is True
    reader = store.reader()
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = reader.day_hypothesis_families() if target.startswith("family") else reader.day_hypothesis_versions()


@pytest.mark.parametrize("record_kind", ("family", "version"))
def test_reads_reject_valid_noncanonical_payload_json(tmp_path: Path, record_kind: str) -> None:
    database = tmp_path / "ledger.sqlite3"
    store = ExperimentLedgerStore(database)
    family = _family()
    version = _version(family)
    with store.writer() as writer:
        assert writer.register_day_hypothesis_family(family) is True
        assert writer.register_day_hypothesis_version(version) is True

    table = "day_hypothesis_families" if record_kind == "family" else "day_hypothesis_versions"
    with sqlite3.connect(database) as connection:
        payload: tuple[str] = connection.execute(f"SELECT payload_json FROM {table}").fetchone()
        noncanonical = json.dumps(json.loads(payload[0]), indent=2, sort_keys=False)
        connection.execute(f"DROP TRIGGER {table}_no_update")
        connection.execute(f"UPDATE {table} SET payload_json=?", (noncanonical,))
        _restore_update_trigger(connection, table)
        connection.commit()

    assert store.is_initialized() is True
    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = (
            store.day_hypothesis_families()
            if record_kind == "family"
            else store.day_hypothesis_versions()
        )


def test_store_reader_connection_is_query_only(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "ledger.sqlite3")
    with store.writer():
        pass
    reader = store.reader()

    with (
        reader._reader_connection() as connection,
        pytest.raises(
            sqlite3.OperationalError,
            match="readonly",
        ),
    ):
        connection.execute("INSERT INTO day_hypothesis_families DEFAULT VALUES")


def _placeholder(column: str) -> str | int | None:
    if column in {"sequence", "search_budget_debit"}:
        return 1
    if column == "market_id":
        return MarketId.US_EQUITIES.value
    if column.endswith("_at"):
        if column == "first_shadow_eligible_at":
            return (CREATED_AT + dt.timedelta(minutes=1)).isoformat()
        return CREATED_AT.isoformat()
    if column.endswith("_date"):
        return CREATED_AT.date().isoformat()
    if column == "payload_json":
        return "{}"
    if column.startswith("parent_") or column == "previous_event_id":
        return None
    return "a" * 64


def _restore_update_trigger(connection: sqlite3.Connection, table: str) -> None:
    connection.execute(
        f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, 'append-only'); END"""
    )
