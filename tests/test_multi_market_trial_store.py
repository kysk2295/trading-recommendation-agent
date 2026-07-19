from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading_agent.experiment_ledger_models import ExperimentTrialEvent, TrialEventKind, TrialKind
from trading_agent.experiment_ledger_schema import (
    CREATE_EXPERIMENT_LEDGER_SCHEMA_V1,
    CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4,
    CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2,
    CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3,
    EXPERIMENT_LEDGER_SCHEMA_VERSION,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
    multi_market_experiment_scope_key,
)
from trading_agent.multi_market_experiment_store import (
    register_multi_market_hypothesis,
    register_multi_market_strategy_version,
)
from trading_agent.multi_market_trial_keys import multi_market_trial_event_key
from trading_agent.multi_market_trial_models import MultiMarketExperimentTrialRegistration
from trading_agent.research_identity_models import AgentOperatingMode

KST = ZoneInfo("Asia/Seoul")
REGISTERED_AT = dt.datetime(2026, 7, 19, 8, 30, tzinfo=KST)
PLANNED_DATE = dt.date(2026, 7, 20)


def _lineage() -> tuple[MultiMarketHypothesisRegistration, MultiMarketStrategyVersionRegistration]:
    scope = MultiMarketExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id="H-KR-THEME-LEADER-VWAP-001",
        primary_lane=KR_THEME_LEADER_VWAP_RECLAIM_LANE,
        lanes=(KR_THEME_LEADER_VWAP_RECLAIM_LANE,),
        registered_at=REGISTERED_AT,
    )
    hypothesis = MultiMarketHypothesisRegistration(
        hypothesis_id=scope.hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=multi_market_experiment_scope_key(scope),
        hypothesis="A fresh rank-one theme leader may reclaim session VWAP.",
        falsification_rule="Reject when fixed forward gates fail against no entry.",
        source_registered_at=REGISTERED_AT,
        ledger_recorded_at=REGISTERED_AT,
    )
    version = MultiMarketStrategyVersionRegistration(
        strategy_version="kr-theme-leader-vwap-reclaim-v1-code-aaaaaaaaaaaaaaaa",
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_scope_key=hypothesis.experiment_scope_key,
        strategy_lane=KR_THEME_LEADER_VWAP_RECLAIM_LANE,
        operating_mode=AgentOperatingMode.SHADOW,
        code_version="checkpoint-code-v1",
        parameter_set=("extension_pct:0.01",),
        data_contract=("completed_minutes:true",),
        cost_model=("slippage_bps:20",),
        portfolio_policy=("order_authority:false",),
        source_registered_at=REGISTERED_AT,
        ledger_recorded_at=REGISTERED_AT,
    )
    return hypothesis, version


def _trial() -> MultiMarketExperimentTrialRegistration:
    hypothesis, version = _lineage()
    return MultiMarketExperimentTrialRegistration(
        trial_id="trial-kr-theme-vwap-20260720",
        strategy_version=version.strategy_version,
        trial_kind=TrialKind.SHADOW_FORWARD,
        experiment_scope=hypothesis.experiment_scope,
        experiment_scope_key=hypothesis.experiment_scope_key,
        strategy_lane=version.strategy_lane,
        evaluator_version="kr-theme-day-forward-v1",
        data_version="b" * 64,
        feed_entitlement="KIS_read_only_domestic_quotes",
        planned_start=PLANNED_DATE,
        planned_end=PLANNED_DATE,
        registered_at=REGISTERED_AT,
        evidence_budget=(
            "counterfactual:no_entry",
            "minimum_forward_sessions:20",
        ),
    )


def _started() -> ExperimentTrialEvent:
    return ExperimentTrialEvent(
        trial_id=_trial().trial_id,
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=dt.datetime(2026, 7, 20, 9, tzinfo=KST),
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )


def _register_lineage(store: ExperimentLedgerStore) -> None:
    hypothesis, version = _lineage()
    with store.writer() as writer:
        assert writer.register_multi_market_hypothesis(hypothesis) is True
        assert writer.register_multi_market_strategy_version(version) is True


def test_multi_market_trial_model_rejects_registration_after_session_open() -> None:
    with pytest.raises(ValidationError):
        _ = MultiMarketExperimentTrialRegistration.model_validate(
            _trial().model_dump(mode="python") | {"registered_at": dt.datetime(2026, 7, 20, 9, 1, tzinfo=KST)}
        )


def test_writer_registers_multi_market_trial_and_event_replay(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)

    with store.writer() as writer:
        assert writer.register_multi_market_trial(_trial()) is True
        assert writer.register_multi_market_trial(_trial()) is False
        assert writer.append_multi_market_trial_event(_started()) is True
        assert writer.append_multi_market_trial_event(_started()) is False

    assert store.multi_market_trials()[0].registration == _trial()
    assert store.multi_market_trial_events(_trial().trial_id)[0].event == _started()


def test_multi_market_trial_requires_exact_parent_and_event_chain(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_multi_market_trial(_trial())

    _register_lineage(store)
    with store.writer() as writer:
        assert writer.register_multi_market_trial(_trial()) is True
        assert writer.append_multi_market_trial_event(_started()) is True

    changed = ExperimentTrialEvent.model_validate(
        _started().model_dump(mode="python") | {"occurred_at": dt.datetime(2026, 7, 20, 9, 1, tzinfo=KST)}
    )
    with pytest.raises(ExperimentLedgerConflictError), store.writer() as writer:
        _ = writer.append_multi_market_trial_event(changed)


def test_multi_market_trial_rejects_preopen_start(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    preopen = ExperimentTrialEvent.model_validate(
        _started().model_dump(mode="python") | {"occurred_at": dt.datetime(2026, 7, 20, 8, 59, tzinfo=KST)}
    )
    with store.writer() as writer:
        assert writer.register_multi_market_trial(_trial()) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_multi_market_trial_event(preopen)


def test_writer_migrates_v4_to_v5_without_rewriting_parent(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    hypothesis, version_registration = _lineage()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            CREATE_EXPERIMENT_LEDGER_SCHEMA_V1
            + CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2
            + CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3
            + CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4
        )
        assert register_multi_market_hypothesis(connection, hypothesis) is True
        assert register_multi_market_strategy_version(connection, version_registration) is True
        _ = connection.execute("PRAGMA user_version = 4")
        connection.commit()
        original = connection.execute(
            "SELECT registration_key, payload_json FROM multi_market_strategy_versions"
        ).fetchone()
    with ExperimentLedgerStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        migrated = connection.execute(
            "SELECT registration_key, payload_json FROM multi_market_strategy_versions"
        ).fetchone()
        new_tables = frozenset(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'multi_market_trial%'"
            ).fetchall()
        )

    assert version == (EXPERIMENT_LEDGER_SCHEMA_VERSION,)
    assert migrated == original
    assert new_tables == {"multi_market_trials", "multi_market_trial_events"}


@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
@pytest.mark.parametrize("table", ("multi_market_trials", "multi_market_trial_events"))
def test_multi_market_trial_tables_are_append_only(tmp_path: Path, operation: str, table: str) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    with store.writer() as writer:
        assert writer.register_multi_market_trial(_trial()) is True
        assert writer.append_multi_market_trial_event(_started()) is True

    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        if operation == "UPDATE":
            _ = connection.execute(f"UPDATE {table} SET payload_json = 'changed'")
        else:
            _ = connection.execute(f"DELETE FROM {table}")


def test_multi_market_trial_event_key_is_content_addressed() -> None:
    assert len(multi_market_trial_event_key(_started())) == 64


def test_multi_market_trial_reader_rejects_missing_append_only_trigger(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    with sqlite3.connect(database) as connection:
        _ = connection.execute("DROP TRIGGER multi_market_trials_no_update")
        connection.commit()

    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.multi_market_trials()
    assert store.is_initialized() is False
