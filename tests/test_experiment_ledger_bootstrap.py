from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from trading_agent.daily_research_contract import strategy_contract, strategy_version_identity
from trading_agent.experiment_ledger_bootstrap import (
    InvalidExperimentLedgerBootstrapSourceError,
    bootstrap_current_intraday_experiments,
)
from trading_agent.experiment_ledger_models import (
    HypothesisRegistration,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerConflictError,
    ExperimentLedgerReader,
    ExperimentLedgerStore,
)
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_contract_models import ExperimentScope, LaneManifest
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
    INTRADAY_MANIFEST,
)
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.research_identity_models import AgentOperatingMode
from trading_agent.strategy_factory import StrategyMode

RECORDED_AT = dt.datetime(2026, 7, 15, 20, tzinfo=dt.UTC)
EFFECTIVE_DATE = dt.date(2026, 7, 16)
CODE_VERSION = "test-code"
ROLLOVER_CODE_VERSION = "next-test-code"


def _seed_lane_registry(
    path: Path,
    *,
    manifests: tuple[LaneManifest, ...] = DEFAULT_LANE_MANIFESTS,
    scopes: tuple[ExperimentScope, ...] = CURRENT_INTRADAY_EXPERIMENT_SCOPES,
) -> LaneRegistryStore:
    store = LaneRegistryStore(path)
    with store.writer() as writer:
        for manifest in manifests:
            _ = writer.register_manifest(manifest)
        for scope in scopes:
            _ = writer.register_experiment_scope(scope)
    return store


def test_bootstrap_registers_four_current_intraday_contracts(tmp_path: Path) -> None:
    lane_registry = _seed_lane_registry(tmp_path / "lane.sqlite3")
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    result = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=CODE_VERSION,
        recorded_at=RECORDED_AT,
    )

    assert result.hypotheses_created == 4
    assert result.versions_created == 4
    assert result.authority_bindings_created == 4
    assert result.lifecycle_events_created == 4
    assert result.effective_session_date == EFFECTIVE_DATE

    reader = ExperimentLedgerReader(experiment_ledger.path)
    hypotheses = reader.hypotheses()
    versions = reader.strategy_versions()
    authorities = reader.strategy_authority_bindings()
    expected_contracts = tuple(strategy_contract(mode) for mode in StrategyMode)
    assert {stored.registration.hypothesis_id for stored in hypotheses} == {
        contract.hypothesis_id for contract in expected_contracts
    }
    assert {stored.registration.strategy_version for stored in versions} == {
        strategy_version_identity(mode, CODE_VERSION) for mode in StrategyMode
    }
    assert {stored.registration.code_version for stored in versions} == {CODE_VERSION}
    assert {stored.binding.operating_mode for stored in authorities} == {AgentOperatingMode.ALPACA_PAPER}
    assert reader.trials() == ()
    for mode, _contract in zip(StrategyMode, expected_contracts, strict=True):
        strategy_version = strategy_version_identity(mode, CODE_VERSION)
        events = reader.lifecycle_events(strategy_version)
        assert len(events) == 1
        assert events[0].event.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
        assert events[0].event.decided_at == RECORDED_AT
        assert reader.lifecycle_state(strategy_version, RECORDED_AT.date()) is None
        assert reader.lifecycle_state(strategy_version, EFFECTIVE_DATE) == events[0]


def test_bootstrap_replay_reuses_original_recording_time(tmp_path: Path) -> None:
    lane_registry = _seed_lane_registry(tmp_path / "lane.sqlite3")
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    first = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=CODE_VERSION,
        recorded_at=RECORDED_AT,
    )

    replay = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=CODE_VERSION,
        recorded_at=RECORDED_AT + dt.timedelta(days=1),
    )

    assert first.hypotheses_created == 4
    assert replay.hypotheses_created == 0
    assert replay.versions_created == 0
    assert replay.authority_bindings_created == 0
    assert replay.lifecycle_events_created == 0
    assert replay.effective_session_date == EFFECTIVE_DATE
    reader = ExperimentLedgerReader(experiment_ledger.path)
    assert {stored.registration.ledger_recorded_at for stored in reader.hypotheses()} == {RECORDED_AT}
    assert {
        reader.lifecycle_events(strategy_version_identity(mode, CODE_VERSION))[0].event.decided_at
        for mode in StrategyMode
    } == {RECORDED_AT}


def test_bootstrap_v2_authority_backfill_binds_at_current_request_time(
    tmp_path: Path,
) -> None:
    lane_registry = _seed_lane_registry(tmp_path / "lane.sqlite3")
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=CODE_VERSION,
        recorded_at=RECORDED_AT,
    )
    with sqlite3.connect(experiment_ledger.path) as connection:
        connection.executescript(
            """
            DROP TRIGGER multi_market_strategy_versions_no_delete;
            DROP TRIGGER multi_market_strategy_versions_no_update;
            DROP TRIGGER multi_market_hypotheses_no_delete;
            DROP TRIGGER multi_market_hypotheses_no_update;
            DROP INDEX multi_market_strategy_versions_by_lane;
            DROP TABLE multi_market_strategy_versions;
            DROP TABLE multi_market_hypotheses;
            DROP TRIGGER strategy_authority_bindings_no_delete;
            DROP TRIGGER strategy_authority_bindings_no_update;
            DROP INDEX strategy_authority_bindings_by_lane;
            DROP TABLE strategy_authority_bindings;
            PRAGMA user_version = 2;
            """
        )
        connection.commit()
    backfilled_at = RECORDED_AT + dt.timedelta(days=1)

    result = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=CODE_VERSION,
        recorded_at=backfilled_at,
    )

    assert result.authority_bindings_created == 4
    assert {stored.binding.bound_at for stored in experiment_ledger.strategy_authority_bindings()} == {backfilled_at}


def test_bootstrap_appends_a_new_complete_version_batch_for_new_code(
    tmp_path: Path,
) -> None:
    lane_registry = _seed_lane_registry(tmp_path / "lane.sqlite3")
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    first = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=CODE_VERSION,
        recorded_at=RECORDED_AT,
    )
    rollover_at = RECORDED_AT + dt.timedelta(days=1)
    rollover = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=ROLLOVER_CODE_VERSION,
        recorded_at=rollover_at,
    )
    replay = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=ROLLOVER_CODE_VERSION,
        recorded_at=rollover_at + dt.timedelta(days=1),
    )

    assert (first.hypotheses_created, first.versions_created, first.lifecycle_events_created) == (4, 4, 4)
    assert (rollover.hypotheses_created, rollover.versions_created, rollover.lifecycle_events_created) == (0, 4, 4)
    assert (replay.hypotheses_created, replay.versions_created, replay.lifecycle_events_created) == (0, 0, 0)
    assert rollover.effective_session_date == dt.date(2026, 7, 17)
    assert replay.effective_session_date == rollover.effective_session_date

    reader = ExperimentLedgerReader(experiment_ledger.path)
    assert len(reader.hypotheses()) == 4
    assert {stored.registration.strategy_version for stored in reader.strategy_versions()} == {
        strategy_version_identity(mode, version)
        for mode in StrategyMode
        for version in (CODE_VERSION, ROLLOVER_CODE_VERSION)
    }
    for mode in StrategyMode:
        original = reader.lifecycle_events(strategy_version_identity(mode, CODE_VERSION))
        rolled = reader.lifecycle_events(strategy_version_identity(mode, ROLLOVER_CODE_VERSION))
        assert original[0].event.decided_at == RECORDED_AT
        assert rolled[0].event.decided_at == rollover_at
        assert rolled[0].event.effective_session_date == rollover.effective_session_date


def test_bootstrap_migrates_v1_ledger_before_appending_code_rollover(
    tmp_path: Path,
) -> None:
    lane_registry = _seed_lane_registry(tmp_path / "lane.sqlite3")
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    first = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=CODE_VERSION,
        recorded_at=RECORDED_AT,
    )
    assert (first.hypotheses_created, first.versions_created, first.lifecycle_events_created) == (4, 4, 4)

    with sqlite3.connect(experiment_ledger.path) as connection:
        connection.executescript(
            """
            DROP TRIGGER multi_market_strategy_versions_no_delete;
            DROP TRIGGER multi_market_strategy_versions_no_update;
            DROP TRIGGER multi_market_hypotheses_no_delete;
            DROP TRIGGER multi_market_hypotheses_no_update;
            DROP INDEX multi_market_strategy_versions_by_lane;
            DROP TABLE multi_market_strategy_versions;
            DROP TABLE multi_market_hypotheses;
            DROP TRIGGER strategy_authority_bindings_no_delete;
            DROP TRIGGER strategy_authority_bindings_no_update;
            DROP INDEX strategy_authority_bindings_by_lane;
            DROP TABLE strategy_authority_bindings;
            DROP TRIGGER research_hypothesis_cards_no_delete;
            DROP TRIGGER research_hypothesis_cards_no_update;
            DROP TRIGGER research_sources_no_delete;
            DROP TRIGGER research_sources_no_update;
            DROP TABLE research_hypothesis_cards;
            DROP TABLE research_sources;
            PRAGMA user_version = 1;
            """
        )
        connection.commit()

    rollover = bootstrap_current_intraday_experiments(
        lane_registry=lane_registry,
        experiment_ledger=experiment_ledger,
        code_version=ROLLOVER_CODE_VERSION,
        recorded_at=RECORDED_AT + dt.timedelta(days=1),
    )

    assert (rollover.hypotheses_created, rollover.versions_created, rollover.lifecycle_events_created) == (0, 4, 4)
    reader = ExperimentLedgerReader(experiment_ledger.path)
    assert reader.is_initialized()
    assert len(reader.strategy_versions()) == 8


@pytest.mark.parametrize(
    "case",
    ("missing_registry", "missing_manifest", "changed_manifest", "missing_scope", "changed_scope"),
)
def test_bootstrap_rejects_missing_or_changed_lane_source_before_experiment_write(
    tmp_path: Path,
    case: str,
) -> None:
    lane_path = tmp_path / "lane.sqlite3"
    if case != "missing_registry":
        manifests = DEFAULT_LANE_MANIFESTS
        scopes = CURRENT_INTRADAY_EXPERIMENT_SCOPES
        if case == "missing_manifest":
            manifests = tuple(manifest for manifest in manifests if manifest != INTRADAY_MANIFEST)
        elif case == "changed_manifest":
            changed = LaneManifest.model_validate(
                INTRADAY_MANIFEST.model_dump(mode="python") | {"ledger_namespace": "execution/changed_intraday"}
            )
            manifests = tuple(changed if manifest == INTRADAY_MANIFEST else manifest for manifest in manifests)
        elif case == "missing_scope":
            scopes = scopes[:-1]
        elif case == "changed_scope":
            original = scopes[-1]
            changed_scope = ExperimentScope.model_validate(
                original.model_dump(mode="python") | {"registered_at": original.registered_at + dt.timedelta(seconds=1)}
            )
            scopes = (*scopes[:-1], changed_scope)
        _ = _seed_lane_registry(lane_path, manifests=manifests, scopes=scopes)
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with pytest.raises(InvalidExperimentLedgerBootstrapSourceError):
        _ = bootstrap_current_intraday_experiments(
            lane_registry=LaneRegistryStore(lane_path),
            experiment_ledger=experiment_ledger,
            code_version=CODE_VERSION,
            recorded_at=RECORDED_AT,
        )

    assert not experiment_ledger.path.exists()
    assert not Path(f"{experiment_ledger.path}.writer.lock").exists()
    if case == "missing_registry":
        assert not lane_path.exists()


@pytest.mark.parametrize(
    ("code_version", "recorded_at"),
    (
        ("bad code", RECORDED_AT),
        (CODE_VERSION, dt.datetime(2026, 7, 15, 20)),
        (CODE_VERSION, dt.datetime(2026, 7, 13, 20, tzinfo=dt.UTC)),
        (CODE_VERSION, dt.datetime(2028, 12, 31, 20, tzinfo=dt.UTC)),
    ),
)
def test_bootstrap_rejects_invalid_identity_time_or_calendar_before_write(
    tmp_path: Path,
    code_version: str,
    recorded_at: dt.datetime,
) -> None:
    lane_registry = _seed_lane_registry(tmp_path / "lane.sqlite3")
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with pytest.raises(InvalidExperimentLedgerBootstrapSourceError):
        _ = bootstrap_current_intraday_experiments(
            lane_registry=lane_registry,
            experiment_ledger=experiment_ledger,
            code_version=code_version,
            recorded_at=recorded_at,
        )

    assert not experiment_ledger.path.exists()


def test_bootstrap_conflict_rolls_back_every_new_registration(tmp_path: Path) -> None:
    lane_registry = _seed_lane_registry(tmp_path / "lane.sqlite3")
    experiment_ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    contract = strategy_contract(StrategyMode.GAP_AND_GO)
    scope = contract.experiment_scope
    conflicting = HypothesisRegistration(
        hypothesis_id=contract.hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=experiment_scope_key(scope),
        primary_lane=scope.primary_lane,
        hypothesis="changed hypothesis with the same immutable identity",
        falsification_rule=contract.falsification_rule,
        source_registered_at=scope.registered_at,
        ledger_recorded_at=RECORDED_AT,
    )
    with experiment_ledger.writer() as writer:
        assert writer.register_hypothesis(conflicting) is True
    bytes_before = experiment_ledger.path.read_bytes()

    with pytest.raises(ExperimentLedgerConflictError):
        _ = bootstrap_current_intraday_experiments(
            lane_registry=lane_registry,
            experiment_ledger=experiment_ledger,
            code_version=CODE_VERSION,
            recorded_at=RECORDED_AT + dt.timedelta(hours=1),
        )

    reader = ExperimentLedgerReader(experiment_ledger.path)
    assert tuple(stored.registration for stored in reader.hypotheses()) == (conflicting,)
    assert reader.strategy_versions() == ()
    assert experiment_ledger.path.read_bytes() == bytes_before
