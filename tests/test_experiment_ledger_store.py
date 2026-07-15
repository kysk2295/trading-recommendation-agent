from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from pathlib import Path

import pytest

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialRegistration,
    HypothesisRegistration,
    StrategyVersionRegistration,
    TrialKind,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
    InactiveExperimentLedgerWriterError,
    InvalidExperimentLedgerSourceError,
    UnsupportedExperimentLedgerSchemaError,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_defaults import current_intraday_experiment_scope
from trading_agent.lane_policy_models import LaneId
from trading_agent.strategy_factory import StrategyMode

ORB_CONTRACT = strategy_contract(StrategyMode.ORB)
ORB_SCOPE = current_intraday_experiment_scope("H-MOM-ORB-001")
SOURCE_REGISTERED_AT = ORB_SCOPE.registered_at
LEDGER_RECORDED_AT = dt.datetime(2026, 7, 15, 12, tzinfo=dt.UTC)


def _hypothesis() -> HypothesisRegistration:
    return HypothesisRegistration(
        hypothesis_id=ORB_CONTRACT.hypothesis_id,
        experiment_scope=ORB_SCOPE,
        experiment_scope_key=experiment_scope_key(ORB_SCOPE),
        primary_lane=LaneId.INTRADAY_MOMENTUM,
        hypothesis=ORB_CONTRACT.hypothesis,
        falsification_rule=ORB_CONTRACT.falsification_rule,
        source_registered_at=SOURCE_REGISTERED_AT,
        ledger_recorded_at=LEDGER_RECORDED_AT,
    )


def _version() -> StrategyVersionRegistration:
    return StrategyVersionRegistration(
        strategy_id=StrategyMode.ORB.value,
        strategy_version=ORB_CONTRACT.strategy_version,
        hypothesis_id=ORB_CONTRACT.hypothesis_id,
        experiment_scope_key=experiment_scope_key(ORB_SCOPE),
        lane_id=LaneId.INTRADAY_MOMENTUM,
        code_version="a" * 40,
        parameter_set=ORB_CONTRACT.parameter_set,
        data_contract=CURRENT_DATA_CONTRACT,
        cost_model=CURRENT_COST_MODEL,
        portfolio_policy=SHADOW_PORTFOLIO_POLICY,
        source_registered_at=SOURCE_REGISTERED_AT,
        ledger_recorded_at=LEDGER_RECORDED_AT,
    )


def _trial() -> ExperimentTrialRegistration:
    return ExperimentTrialRegistration(
        trial_id="trial-orb-shadow-20260716",
        strategy_version=ORB_CONTRACT.strategy_version,
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=ORB_SCOPE,
        experiment_scope_key=experiment_scope_key(ORB_SCOPE),
        evaluator_version="paper_metrics_day_block_bootstrap_v2",
        data_version="b" * 64,
        feed_entitlement="KIS_read_only_rankings",
        planned_start=dt.date(2026, 7, 16),
        planned_end=dt.date(2026, 7, 17),
        registered_at=LEDGER_RECORDED_AT,
        evidence_budget=(
            "minimum_completed_trades:100",
            "minimum_forward_sessions:60",
        ),
    )


def _register_lineage(store: ExperimentLedgerStore) -> None:
    with store.writer() as writer:
        assert writer.register_hypothesis(_hypothesis()) is True
        assert writer.register_strategy_version(_version()) is True
        assert writer.register_trial(_trial()) is True


def test_registers_exact_lineage_and_replays_without_duplicate_rows(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)

    with store.writer() as writer:
        assert writer.register_hypothesis(_hypothesis()) is True
        assert writer.register_hypothesis(_hypothesis()) is False
        assert writer.register_strategy_version(_version()) is True
        assert writer.register_strategy_version(_version()) is False
        assert writer.register_trial(_trial()) is True
        assert writer.register_trial(_trial()) is False

    reader = ExperimentLedgerReader(database)

    assert reader.is_initialized() is True
    assert reader.hypotheses()[0].registration == _hypothesis()
    assert reader.strategy_versions()[0].registration == _version()
    assert reader.trials()[0].registration == _trial()
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{database}.writer.lock").stat().st_mode) == 0o600


def test_missing_reader_is_empty_and_does_not_create_paths(tmp_path: Path) -> None:
    database = tmp_path / "missing" / "experiment.sqlite3"
    reader = ExperimentLedgerReader(database)

    assert reader.is_initialized() is False
    assert reader.hypotheses() == ()
    assert reader.strategy_versions() == ()
    assert reader.trials() == ()
    assert not database.exists()
    assert not database.parent.exists()


def test_writer_context_rolls_back_every_registration_on_error(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)

    with pytest.raises(RuntimeError, match="abort batch"), store.writer() as writer:
        assert writer.register_hypothesis(_hypothesis()) is True
        assert writer.register_strategy_version(_version()) is True
        raise RuntimeError("abort batch")

    reader = ExperimentLedgerReader(database)
    assert reader.hypotheses() == ()
    assert reader.strategy_versions() == ()


def test_second_writer_fails_nonblocking_and_closed_writer_is_inactive(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with store.writer() as writer:
        with pytest.raises(ExperimentLedgerWriterLeaseUnavailableError), store.writer():
            pass
        assert writer.register_hypothesis(_hypothesis()) is True

    with pytest.raises(InactiveExperimentLedgerWriterError):
        _ = writer.register_hypothesis(_hypothesis())


def test_reader_connection_is_query_only(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    _register_lineage(ExperimentLedgerStore(database))
    reader = ExperimentLedgerReader(database)

    with (
        reader._reader_connection() as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        _ = connection.execute("DELETE FROM hypotheses")


@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
def test_registration_tables_are_append_only(tmp_path: Path, operation: str) -> None:
    database = tmp_path / "experiment.sqlite3"
    _register_lineage(ExperimentLedgerStore(database))

    with (
        sqlite3.connect(database) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        if operation == "UPDATE":
            _ = connection.execute("UPDATE hypotheses SET hypothesis_id = 'changed'")
        else:
            _ = connection.execute("DELETE FROM hypotheses")


def test_identity_conflict_does_not_change_existing_registration(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    with store.writer() as writer:
        assert writer.register_hypothesis(_hypothesis()) is True

    changed = HypothesisRegistration.model_validate(
        _hypothesis().model_dump(mode="python") | {"hypothesis": "changed but same immutable identity"}
    )
    with pytest.raises(ExperimentLedgerConflictError), store.writer() as writer:
        _ = writer.register_hypothesis(changed)

    assert ExperimentLedgerReader(database).hypotheses()[0].registration == _hypothesis()


def test_version_and_trial_require_exact_parent_lineage(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    wrong_scope = "f" * 64
    other_scope = current_intraday_experiment_scope("H-MOM-VWAP-001")
    wrong_version = StrategyVersionRegistration.model_validate(
        _version().model_dump(mode="python") | {"experiment_scope_key": wrong_scope}
    )
    wrong_trial = ExperimentTrialRegistration.model_validate(
        _trial().model_dump(mode="python")
        | {
            "experiment_scope": other_scope,
            "experiment_scope_key": experiment_scope_key(other_scope),
        }
    )

    with store.writer() as writer:
        assert writer.register_hypothesis(_hypothesis()) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.register_strategy_version(wrong_version)
        assert writer.register_strategy_version(_version()) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.register_trial(wrong_trial)


@pytest.mark.parametrize("corruption", ("payload", "key"))
def test_reader_detects_hypothesis_payload_or_key_corruption(
    tmp_path: Path,
    corruption: str,
) -> None:
    database = tmp_path / "experiment.sqlite3"
    _register_lineage(ExperimentLedgerStore(database))
    with sqlite3.connect(database) as connection:
        _ = connection.execute("DROP TRIGGER hypotheses_no_update")
        if corruption == "payload":
            _ = connection.execute("UPDATE hypotheses SET payload_json = '{}' ")
        else:
            _ = connection.execute("UPDATE hypotheses SET registration_key = ?", ("0" * 64,))
        connection.commit()

    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = ExperimentLedgerReader(database).hypotheses()


def test_unsupported_or_unknown_schema_is_rejected(tmp_path: Path) -> None:
    unsupported = tmp_path / "unsupported.sqlite3"
    with sqlite3.connect(unsupported) as connection:
        _ = connection.execute("PRAGMA user_version = 99")

    with pytest.raises(UnsupportedExperimentLedgerSchemaError):
        _ = ExperimentLedgerReader(unsupported).hypotheses()

    unknown = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(unknown) as connection:
        _ = connection.execute("CREATE TABLE unrelated(value TEXT)")

    with pytest.raises(UnsupportedExperimentLedgerSchemaError), ExperimentLedgerStore(unknown).writer():
        pass
