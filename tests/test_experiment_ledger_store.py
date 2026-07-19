from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from pathlib import Path

import pytest

import trading_agent.experiment_ledger_store as experiment_ledger_store
from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
)
from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
    experiment_trial_event_key,
    hypothesis_registration_key,
    research_hypothesis_card_key,
    research_source_key,
    strategy_authority_binding_key,
    strategy_lifecycle_event_key,
    strategy_version_registration_key,
)
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    HypothesisRegistration,
    ResearchHypothesisCard,
    ResearchSource,
    ResearchSourceKind,
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    StrategyVersionRegistration,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_schema import (
    CREATE_EXPERIMENT_LEDGER_SCHEMA_V1,
    CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2,
    CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3,
    EXPERIMENT_LEDGER_SCHEMA_VERSION,
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
from trading_agent.experiment_scope_models import ExperimentScopeKind
from trading_agent.lane_contract_keys import experiment_scope_key
from trading_agent.lane_defaults import current_intraday_experiment_scope
from trading_agent.lane_policy_models import LaneId
from trading_agent.multi_market_experiment_models import (
    MultiMarketExperimentScope,
    MultiMarketHypothesisRegistration,
    MultiMarketStrategyVersionRegistration,
    multi_market_experiment_scope_key,
)
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentOperatingMode,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.strategy_authority_models import StrategyAuthorityBinding
from trading_agent.strategy_factory import StrategyMode

ORB_CONTRACT = strategy_contract(StrategyMode.ORB)
ORB_SCOPE = current_intraday_experiment_scope("H-MOM-ORB-001")
SOURCE_REGISTERED_AT = ORB_SCOPE.registered_at
LEDGER_RECORDED_AT = dt.datetime(2026, 7, 15, 12, tzinfo=dt.UTC)
RESEARCH_SOURCE_RECORDED_AT = SOURCE_REGISTERED_AT - dt.timedelta(seconds=1)
STARTED_AT = dt.datetime(2026, 7, 16, 13, 20, tzinfo=dt.UTC)
DECIDED_AT = dt.datetime(2026, 7, 15, 20, tzinfo=dt.UTC)
DECISION_DATE = dt.date(2026, 7, 15)
EFFECTIVE_DATE = dt.date(2026, 7, 16)


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


def _research_source() -> ResearchSource:
    return ResearchSource(
        source_id="academic-momentum-1993",
        source_kind=ResearchSourceKind.ACADEMIC_PAPER,
        title="Returns to Buying Winners and Selling Losers",
        source_url="https://doi.org/10.1111/j.1540-6261.1993.tb04702.x",
        published_on=dt.date(1993, 2, 1),
        claim="Intermediate-horizon relative strength motivates a momentum trial.",
        limitations="It is not current-market or net-cost evidence for this project.",
        retrieved_at=RESEARCH_SOURCE_RECORDED_AT,
        ledger_recorded_at=RESEARCH_SOURCE_RECORDED_AT,
    )


def _research_card() -> ResearchHypothesisCard:
    return ResearchHypothesisCard(
        hypothesis=_hypothesis(),
        research_source_keys=(str(research_source_key(_research_source())),),
        economic_mechanism="Underreaction may leave return continuation.",
        counterfactual_baseline="Matched eligible-universe forward return after the same cost model.",
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


def _strategy_authority_binding(
    operating_mode: AgentOperatingMode = AgentOperatingMode.ALPACA_PAPER,
) -> StrategyAuthorityBinding:
    return StrategyAuthorityBinding(
        strategy_version=ORB_CONTRACT.strategy_version,
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id=StrategyMode.ORB.value,
        ),
        operating_mode=operating_mode,
        legacy_lane_id=LaneId.INTRADAY_MOMENTUM,
        bound_at=LEDGER_RECORDED_AT,
    )


def _kr_multi_market_hypothesis() -> MultiMarketHypothesisRegistration:
    registered_at = dt.datetime(2026, 7, 19, 8, tzinfo=dt.UTC)
    lane = StrategyLaneRef(
        market_id=MarketId.KR_EQUITIES,
        agent_family=AgentFamily.OPPORTUNITY_MANAGER,
        strategy_id="theme_momentum",
    )
    scope = MultiMarketExperimentScope(
        scope_kind=ExperimentScopeKind.SINGLE_LANE,
        hypothesis_id="H-KR-THEME-MOMENTUM-001",
        primary_lane=lane,
        lanes=(lane,),
        registered_at=registered_at,
    )
    return MultiMarketHypothesisRegistration(
        hypothesis_id=scope.hypothesis_id,
        experiment_scope=scope,
        experiment_scope_key=multi_market_experiment_scope_key(scope),
        hypothesis="Fresh corroborated KR themes may produce ranked opportunities.",
        falsification_rule="Reject when forward ranking has no net-cost information.",
        source_registered_at=registered_at,
        ledger_recorded_at=registered_at,
    )


def _kr_multi_market_version() -> MultiMarketStrategyVersionRegistration:
    hypothesis = _kr_multi_market_hypothesis()
    return MultiMarketStrategyVersionRegistration(
        strategy_version="kr_theme_momentum_v1",
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_scope_key=hypothesis.experiment_scope_key,
        strategy_lane=hypothesis.experiment_scope.primary_lane,
        operating_mode=AgentOperatingMode.SHADOW,
        code_version="kr-theme-code-v1",
        parameter_set=("freshness_seconds:900",),
        data_contract=("kr_theme_evidence_v1",),
        cost_model=("opportunity_only",),
        portfolio_policy=("no_order_authority",),
        source_registered_at=hypothesis.source_registered_at,
        ledger_recorded_at=hypothesis.ledger_recorded_at,
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


def _started_event(*, occurred_at: dt.datetime = STARTED_AT) -> ExperimentTrialEvent:
    return ExperimentTrialEvent(
        trial_id=_trial().trial_id,
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=occurred_at,
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )


def _terminal_event(
    previous: ExperimentTrialEvent,
    *,
    event_kind: TrialEventKind = TrialEventKind.COMPLETED,
    sequence: int | None = None,
    previous_event_key: str | None = None,
    occurred_at: dt.datetime | None = None,
) -> ExperimentTrialEvent:
    completed = event_kind is TrialEventKind.COMPLETED
    return ExperimentTrialEvent(
        trial_id=previous.trial_id,
        sequence=previous.sequence + 1 if sequence is None else sequence,
        event_kind=event_kind,
        occurred_at=(previous.occurred_at + dt.timedelta(hours=7)) if occurred_at is None else occurred_at,
        artifact_sha256s=("c" * 64,) if completed else (),
        reason_codes=() if completed else ("source_failure",),
        previous_event_key=(experiment_trial_event_key(previous) if previous_event_key is None else previous_event_key),
    )


def _lifecycle_registration() -> StrategyLifecycleEvent:
    evidence_keys = tuple(
        sorted(
            (
                str(experiment_scope_key(ORB_SCOPE)),
                str(hypothesis_registration_key(_hypothesis())),
                str(strategy_version_registration_key(_version())),
            )
        )
    )
    return StrategyLifecycleEvent(
        strategy_version=_version().strategy_version,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="strategy_lifecycle_v1",
        decision_session_date=DECISION_DATE,
        effective_session_date=EFFECTIVE_DATE,
        decided_at=DECIDED_AT,
        evidence_keys=evidence_keys,
        reason_codes=("existing_contract_import",),
        previous_event_key=None,
    )


def _lifecycle_transition(
    previous: StrategyLifecycleEvent,
    *,
    to_state: StrategyLifecycleState,
    decision_session_date: dt.date,
    effective_session_date: dt.date,
    decided_at: dt.datetime,
) -> StrategyLifecycleEvent:
    return StrategyLifecycleEvent(
        strategy_version=previous.strategy_version,
        sequence=previous.sequence + 1,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=previous.to_state,
        to_state=to_state,
        policy_version=previous.policy_version,
        decision_session_date=decision_session_date,
        effective_session_date=effective_session_date,
        decided_at=decided_at,
        evidence_keys=("e" * 64,),
        reason_codes=("review_evidence_verified",),
        previous_event_key=strategy_lifecycle_event_key(previous),
    )


def _register_lineage(store: ExperimentLedgerStore) -> None:
    with store.writer() as writer:
        assert writer.register_hypothesis(_hypothesis()) is True
        assert writer.register_strategy_version(_version()) is True
        assert writer.register_trial(_trial()) is True


def test_registers_research_source_and_card_with_exact_replay(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    source = _research_source()
    card = _research_card()
    store = ExperimentLedgerStore(database)

    with store.writer() as writer:
        assert writer.register_research_source(source) is True
        assert writer.register_research_source(source) is False
        assert writer.register_research_hypothesis(card) is True
        assert writer.register_research_hypothesis(card) is False

    reader = ExperimentLedgerReader(database)

    assert reader.research_sources()[0].source_key == research_source_key(source)
    assert reader.research_sources()[0].source == source
    assert reader.research_hypothesis_cards()[0].card_key == research_hypothesis_card_key(card)
    assert reader.research_hypothesis_cards()[0].card == card
    assert reader.hypotheses()[0].registration == card.hypothesis


def test_research_hypothesis_rejects_unknown_source(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_research_hypothesis(_research_card())


def test_research_hypothesis_rejects_source_recorded_after_scope_preregistration(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    late_recorded_at = _hypothesis().source_registered_at + dt.timedelta(seconds=1)
    late_source = ResearchSource.model_validate(
        _research_source().model_dump(mode="python")
        | {
            "retrieved_at": late_recorded_at,
            "ledger_recorded_at": late_recorded_at,
        }
    )
    card = ResearchHypothesisCard(
        hypothesis=_hypothesis(),
        research_source_keys=(str(research_source_key(late_source)),),
        economic_mechanism="Underreaction may leave return continuation.",
        counterfactual_baseline="Matched eligible-universe forward return after the same cost model.",
    )

    with store.writer() as writer:
        assert writer.register_research_source(late_source) is True

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_research_hypothesis(card)


def test_writer_migrates_v1_without_rewriting_existing_rows(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    hypothesis = _hypothesis()
    original_row = (
        str(hypothesis_registration_key(hypothesis)),
        hypothesis.hypothesis_id,
        hypothesis.experiment_scope_key,
        hypothesis.primary_lane.value,
        hypothesis.model_dump_json(),
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_EXPERIMENT_LEDGER_SCHEMA_V1)
        _ = connection.execute("PRAGMA user_version = 1")
        _ = connection.execute("INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?)", original_row)
        connection.commit()

    with ExperimentLedgerStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        migrated_row = connection.execute(
            "SELECT registration_key, hypothesis_id, experiment_scope_key, lane_id, payload_json FROM hypotheses"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()

    assert migrated_row == original_row
    assert version == (5,)


def test_writer_migrates_v2_without_rewriting_existing_rows(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    hypothesis = _hypothesis()
    original_row = (
        str(hypothesis_registration_key(hypothesis)),
        hypothesis.hypothesis_id,
        hypothesis.experiment_scope_key,
        hypothesis.primary_lane.value,
        hypothesis.model_dump_json(),
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_EXPERIMENT_LEDGER_SCHEMA_V1 + CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2)
        _ = connection.execute("PRAGMA user_version = 2")
        _ = connection.execute("INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?)", original_row)
        connection.commit()

    with ExperimentLedgerStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        migrated_row = connection.execute(
            "SELECT registration_key, hypothesis_id, experiment_scope_key, lane_id, payload_json FROM hypotheses"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()

    assert migrated_row == original_row
    assert version == (5,)


def test_writer_migrates_v3_without_rewriting_existing_rows(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    hypothesis = _hypothesis()
    original_row = (
        str(hypothesis_registration_key(hypothesis)),
        hypothesis.hypothesis_id,
        hypothesis.experiment_scope_key,
        hypothesis.primary_lane.value,
        hypothesis.model_dump_json(),
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            CREATE_EXPERIMENT_LEDGER_SCHEMA_V1
            + CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2
            + CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3
        )
        _ = connection.execute("PRAGMA user_version = 3")
        _ = connection.execute("INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?)", original_row)
        connection.commit()

    with ExperimentLedgerStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        migrated_row = connection.execute(
            "SELECT registration_key, hypothesis_id, experiment_scope_key, lane_id, payload_json FROM hypotheses"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()

    assert migrated_row == original_row
    assert version == (5,)


def test_current_experiment_ledger_schema_is_v5() -> None:
    assert EXPERIMENT_LEDGER_SCHEMA_VERSION == 5


def test_writer_rolls_back_v1_migration_when_v2_ddl_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "experiment.sqlite3"
    hypothesis = _hypothesis()
    original_row = (
        str(hypothesis_registration_key(hypothesis)),
        hypothesis.hypothesis_id,
        hypothesis.experiment_scope_key,
        hypothesis.primary_lane.value,
        hypothesis.model_dump_json(),
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_EXPERIMENT_LEDGER_SCHEMA_V1)
        _ = connection.execute("PRAGMA user_version = 1")
        _ = connection.execute("INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?)", original_row)
        connection.commit()

    monkeypatch.setattr(
        experiment_ledger_store,
        "CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2",
        "CREATE TABLE migration_test_marker (value INTEGER); CREATE TABLE hypotheses (value INTEGER);",
    )

    with pytest.raises(sqlite3.OperationalError, match="already exists"), ExperimentLedgerStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        objects = frozenset(
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        )
        migrated_row = connection.execute(
            "SELECT registration_key, hypothesis_id, experiment_scope_key, lane_id, payload_json FROM hypotheses"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()

    assert objects == experiment_ledger_store._V1_SCHEMA_OBJECTS
    assert migrated_row == original_row
    assert version == (1,)


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


def test_writer_registers_strategy_authority_binding(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)

    with store.writer() as writer:
        assert writer.register_strategy_authority_binding(_strategy_authority_binding()) is True
        assert writer.register_strategy_authority_binding(_strategy_authority_binding()) is False

    stored = ExperimentLedgerReader(store.path).strategy_authority_bindings()

    assert stored[0].binding_key == strategy_authority_binding_key(_strategy_authority_binding())
    assert stored[0].binding == _strategy_authority_binding()


def test_writer_registers_multi_market_hypothesis(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with store.writer() as writer:
        assert writer.register_multi_market_hypothesis(_kr_multi_market_hypothesis()) is True
        assert writer.register_multi_market_hypothesis(_kr_multi_market_hypothesis()) is False
        assert writer.register_multi_market_strategy_version(_kr_multi_market_version()) is True
        assert writer.register_multi_market_strategy_version(_kr_multi_market_version()) is False

    assert store.multi_market_hypotheses()[0].registration == _kr_multi_market_hypothesis()
    assert store.multi_market_strategy_versions()[0].registration == _kr_multi_market_version()


def test_multi_market_version_rejects_unknown_parent(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_multi_market_strategy_version(_kr_multi_market_version())


def test_strategy_authority_binding_rejects_conflicting_mode(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    shadow = StrategyAuthorityBinding.model_validate(
        _strategy_authority_binding().model_dump(mode="python") | {"operating_mode": AgentOperatingMode.SHADOW}
    )

    with store.writer() as writer:
        assert writer.register_strategy_authority_binding(_strategy_authority_binding()) is True

    with pytest.raises(ExperimentLedgerConflictError), store.writer() as writer:
        _ = writer.register_strategy_authority_binding(shadow)


@pytest.mark.parametrize(
    "change",
    (
        {
            "strategy_lane": StrategyLaneRef(
                market_id=MarketId.US_EQUITIES,
                agent_family=AgentFamily.DAY_TRADING,
                strategy_id="different_strategy",
            )
        },
        {"bound_at": LEDGER_RECORDED_AT - dt.timedelta(microseconds=1)},
    ),
)
def test_strategy_authority_binding_rejects_invalid_parent(
    tmp_path: Path,
    change: dict[str, StrategyLaneRef | dt.datetime],
) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    binding = StrategyAuthorityBinding.model_validate(_strategy_authority_binding().model_dump(mode="python") | change)

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.register_strategy_authority_binding(binding)


def test_shadow_authority_rejects_paper_champion(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    binding = _strategy_authority_binding(AgentOperatingMode.SHADOW)
    registration = _lifecycle_registration()
    paper = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.EXPERIMENTAL_PAPER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    challenger = _lifecycle_transition(
        paper,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=dt.date(2026, 7, 17),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 17, 20, tzinfo=dt.UTC),
    )
    champion = _authorized_champion_transition(
        challenger,
        binding,
        StrategyLifecycleState.PAPER_CHAMPION,
    )

    with store.writer() as writer:
        assert writer.register_strategy_authority_binding(binding) is True
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(paper) is True
        assert writer.append_lifecycle_event(challenger) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(champion)


def test_paper_authority_rejects_shadow_champion(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    binding = _strategy_authority_binding()
    registration = _lifecycle_registration()
    challenger = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    champion = _authorized_champion_transition(
        challenger,
        binding,
        StrategyLifecycleState.SHADOW_CHAMPION,
    )

    with store.writer() as writer:
        assert writer.register_strategy_authority_binding(binding) is True
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(challenger) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(champion)


def test_shadow_authority_appends_and_reads_shadow_champion(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    binding = _strategy_authority_binding(AgentOperatingMode.SHADOW)
    registration = _lifecycle_registration()
    challenger = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    champion = _authorized_champion_transition(
        challenger,
        binding,
        StrategyLifecycleState.SHADOW_CHAMPION,
    )

    with store.writer() as writer:
        assert writer.register_strategy_authority_binding(binding) is True
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(challenger) is True
        assert writer.append_lifecycle_event(champion) is True

    assert store.lifecycle_events(champion.strategy_version)[-1].event == champion


def test_paper_authority_requires_paper_phase_before_paper_champion(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    binding = _strategy_authority_binding()
    registration = _lifecycle_registration()
    challenger = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    champion = _authorized_champion_transition(
        challenger,
        binding,
        StrategyLifecycleState.PAPER_CHAMPION,
    )

    with store.writer() as writer:
        assert writer.register_strategy_authority_binding(binding) is True
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(challenger) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(champion)


def test_paper_authority_appends_paper_champion_after_paper_phase(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    binding = _strategy_authority_binding()
    registration = _lifecycle_registration()
    paper = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.EXPERIMENTAL_PAPER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    challenger = _lifecycle_transition(
        paper,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=dt.date(2026, 7, 17),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 17, 20, tzinfo=dt.UTC),
    )
    champion = _authorized_champion_transition(
        challenger,
        binding,
        StrategyLifecycleState.PAPER_CHAMPION,
    )

    with store.writer() as writer:
        assert writer.register_strategy_authority_binding(binding) is True
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(paper) is True
        assert writer.append_lifecycle_event(challenger) is True
        assert writer.append_lifecycle_event(champion) is True

    assert store.lifecycle_events(champion.strategy_version)[-1].event == champion


@pytest.mark.parametrize("register_binding", (False, True))
def test_champion_requires_registered_binding_and_exact_evidence_key(
    tmp_path: Path,
    register_binding: bool,
) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    binding = _strategy_authority_binding(AgentOperatingMode.SHADOW)
    registration = _lifecycle_registration()
    challenger = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    champion = _authorized_champion_transition(
        challenger,
        binding,
        StrategyLifecycleState.SHADOW_CHAMPION,
    )
    if register_binding:
        champion = StrategyLifecycleEvent.model_validate(
            champion.model_dump(mode="python") | {"evidence_keys": ("e" * 64,)}
        )

    with store.writer() as writer:
        if register_binding:
            assert writer.register_strategy_authority_binding(binding) is True
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(challenger) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(champion)


def test_reader_preserves_legacy_paper_champion_without_authority_binding(
    tmp_path: Path,
) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    registration = _lifecycle_registration()
    challenger = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    legacy = _authorized_champion_transition(
        challenger,
        _strategy_authority_binding(),
        StrategyLifecycleState.PAPER_CHAMPION,
    )
    with store.writer() as writer:
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(challenger) is True
    _insert_lifecycle_event(database, legacy)

    assert store.lifecycle_events(legacy.strategy_version)[-1].event == legacy


def test_reader_rejects_shadow_champion_without_authority_binding(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    registration = _lifecycle_registration()
    challenger = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    invalid = _authorized_champion_transition(
        challenger,
        _strategy_authority_binding(AgentOperatingMode.SHADOW),
        StrategyLifecycleState.SHADOW_CHAMPION,
    )
    with store.writer() as writer:
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(challenger) is True
    _insert_lifecycle_event(database, invalid)

    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = store.lifecycle_events(invalid.strategy_version)


def _insert_lifecycle_event(database: Path, event: StrategyLifecycleEvent) -> None:
    key = strategy_lifecycle_event_key(event)
    with sqlite3.connect(database) as connection:
        _ = connection.execute(
            "INSERT INTO strategy_lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                event.strategy_version,
                event.sequence,
                event.event_kind.value,
                event.effective_session_date.isoformat(),
                event.previous_event_key,
                canonical_experiment_ledger_json(event),
            ),
        )
        connection.commit()


def _authorized_champion_transition(
    challenger: StrategyLifecycleEvent,
    binding: StrategyAuthorityBinding,
    champion: StrategyLifecycleState,
) -> StrategyLifecycleEvent:
    decision_date = challenger.effective_session_date
    days_to_next_session = 3 if decision_date.weekday() == 4 else 1
    transition = _lifecycle_transition(
        challenger,
        to_state=champion,
        decision_session_date=decision_date,
        effective_session_date=decision_date + dt.timedelta(days=days_to_next_session),
        decided_at=dt.datetime.combine(decision_date, dt.time(20), dt.UTC),
    )
    return StrategyLifecycleEvent.model_validate(
        transition.model_dump(mode="python")
        | {"evidence_keys": tuple(sorted((*transition.evidence_keys, str(strategy_authority_binding_key(binding)))))}
    )


def test_missing_reader_is_empty_and_does_not_create_paths(tmp_path: Path) -> None:
    database = tmp_path / "missing" / "experiment.sqlite3"
    reader = ExperimentLedgerReader(database)

    assert reader.is_initialized() is False
    assert reader.hypotheses() == ()
    assert reader.strategy_versions() == ()
    assert reader.strategy_authority_bindings() == ()
    assert reader.multi_market_hypotheses() == ()
    assert reader.multi_market_strategy_versions() == ()
    assert reader.multi_market_trials() == ()
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


def test_reader_connection_closes_after_context(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    _register_lineage(ExperimentLedgerStore(database))
    reader = ExperimentLedgerReader(database)

    with reader._reader_connection() as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        _ = connection.execute("SELECT 1")


@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
@pytest.mark.parametrize(
    "table",
    (
        "hypotheses",
        "research_sources",
        "research_hypothesis_cards",
        "strategy_authority_bindings",
        "multi_market_hypotheses",
        "multi_market_strategy_versions",
    ),
)
def test_registration_tables_are_append_only(tmp_path: Path, operation: str, table: str) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    with store.writer() as writer:
        assert writer.register_research_source(_research_source()) is True
        assert writer.register_research_hypothesis(_research_card()) is True
        assert writer.register_strategy_authority_binding(_strategy_authority_binding()) is True
        assert writer.register_multi_market_hypothesis(_kr_multi_market_hypothesis()) is True
        assert writer.register_multi_market_strategy_version(_kr_multi_market_version()) is True

    with (
        sqlite3.connect(database) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        if operation == "UPDATE":
            _ = connection.execute(f"UPDATE {table} SET payload_json = 'changed'")
        else:
            _ = connection.execute(f"DELETE FROM {table}")


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


def test_trial_event_chain_appends_replays_and_reads_in_sequence(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    started = _started_event()
    completed = _terminal_event(started)

    with store.writer() as writer:
        assert writer.append_trial_event(started) is True
        assert writer.append_trial_event(started) is False
        assert writer.append_trial_event(completed) is True
        assert writer.append_trial_event(completed) is False

    events = ExperimentLedgerReader(database).trial_events(_trial().trial_id)

    assert tuple(stored.event for stored in events) == (started, completed)
    assert tuple(stored.event_key for stored in events) == (
        experiment_trial_event_key(started),
        experiment_trial_event_key(completed),
    )


@pytest.mark.parametrize(
    "candidate",
    (
        _terminal_event(_started_event(), sequence=3),
        _terminal_event(_started_event(), previous_event_key="f" * 64),
        _terminal_event(
            _started_event(),
            occurred_at=_started_event().occurred_at - dt.timedelta(microseconds=1),
        ),
    ),
)
def test_trial_event_chain_rejects_gap_wrong_parent_or_time_reversal(
    tmp_path: Path,
    candidate: ExperimentTrialEvent,
) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)

    with store.writer() as writer:
        assert writer.append_trial_event(_started_event()) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_trial_event(candidate)


def test_trial_event_rejects_time_before_registration(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    before_registration = _started_event(occurred_at=_trial().registered_at - dt.timedelta(microseconds=1))

    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.append_trial_event(before_registration)


def test_trial_event_terminal_is_final_and_cannot_fork(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    started = _started_event()
    completed = _terminal_event(started)
    after_terminal = _terminal_event(completed, event_kind=TrialEventKind.FAILED)
    fork = _terminal_event(started, event_kind=TrialEventKind.FAILED)

    with store.writer() as writer:
        assert writer.append_trial_event(started) is True
        assert writer.append_trial_event(completed) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_trial_event(after_terminal)
        with pytest.raises(ExperimentLedgerConflictError):
            _ = writer.append_trial_event(fork)


def test_trial_event_identity_conflict_preserves_original(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    started = _started_event()
    changed = ExperimentTrialEvent.model_validate(
        started.model_dump(mode="python") | {"occurred_at": started.occurred_at + dt.timedelta(seconds=1)}
    )

    with store.writer() as writer:
        assert writer.append_trial_event(started) is True
        with pytest.raises(ExperimentLedgerConflictError):
            _ = writer.append_trial_event(changed)

    assert ExperimentLedgerReader(database).trial_events(started.trial_id)[0].event == started


def test_lifecycle_registration_replays_and_projects_only_when_effective(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    registration = _lifecycle_registration()

    with store.writer() as writer:
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(registration) is False

    reader = ExperimentLedgerReader(database)
    events = reader.lifecycle_events(registration.strategy_version)

    assert tuple(stored.event for stored in events) == (registration,)
    assert events[0].event_key == strategy_lifecycle_event_key(registration)
    assert reader.lifecycle_state(registration.strategy_version, DECISION_DATE) is None
    projected = reader.lifecycle_state(registration.strategy_version, EFFECTIVE_DATE)
    assert projected is not None
    assert projected.event.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW


@pytest.mark.parametrize(
    "changes",
    (
        {"sequence": 3},
        {"previous_event_key": "f" * 64},
        {
            "from_state": StrategyLifecycleState.HISTORICAL,
            "to_state": StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        },
    ),
)
def test_lifecycle_chain_rejects_gap_wrong_parent_or_from_state(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    registration = _lifecycle_registration()
    transition = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.EXPERIMENTAL_PAPER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    candidate = StrategyLifecycleEvent.model_validate(transition.model_dump(mode="python") | changes)

    with store.writer() as writer:
        assert writer.append_lifecycle_event(registration) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(candidate)


def test_lifecycle_rejects_transition_while_latest_event_is_pending(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    registration = _lifecycle_registration()
    first_transition = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.EXPERIMENTAL_PAPER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    pending_successor = _lifecycle_transition(
        first_transition,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 21, tzinfo=dt.UTC),
    )

    with store.writer() as writer:
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(first_transition) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(pending_successor)


def test_lifecycle_suspended_recovery_cannot_advance_above_previous_state(
    tmp_path: Path,
) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    registration = _lifecycle_registration()
    suspended = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.SUSPENDED,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    invalid_recovery = _lifecycle_transition(
        suspended,
        to_state=StrategyLifecycleState.CHALLENGER,
        decision_session_date=dt.date(2026, 7, 17),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 17, 20, tzinfo=dt.UTC),
    )
    terminal_rejection = _lifecycle_transition(
        suspended,
        to_state=StrategyLifecycleState.REJECTED,
        decision_session_date=dt.date(2026, 7, 17),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 17, 20, tzinfo=dt.UTC),
    )

    with store.writer() as writer:
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(suspended) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(invalid_recovery)
        assert writer.append_lifecycle_event(terminal_rejection) is True


def test_lifecycle_suspended_recovery_restores_prior_state_by_effective_date(
    tmp_path: Path,
) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    registration = _lifecycle_registration()
    suspended = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.SUSPENDED,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    restored = _lifecycle_transition(
        suspended,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        decision_session_date=dt.date(2026, 7, 17),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 17, 20, tzinfo=dt.UTC),
    )

    with store.writer() as writer:
        assert writer.append_lifecycle_event(registration) is True
        assert writer.append_lifecycle_event(suspended) is True
        assert writer.append_lifecycle_event(restored) is True

    reader = ExperimentLedgerReader(database)
    expected = (
        (EFFECTIVE_DATE, StrategyLifecycleState.EXPERIMENTAL_SHADOW),
        (dt.date(2026, 7, 17), StrategyLifecycleState.SUSPENDED),
        (dt.date(2026, 7, 20), StrategyLifecycleState.EXPERIMENTAL_SHADOW),
    )
    for as_of, state in expected:
        projected = reader.lifecycle_state(registration.strategy_version, as_of)
        assert projected is not None
        assert projected.event.to_state is state


def test_lifecycle_revalidates_invalid_transition_and_rejected_is_terminal(
    tmp_path: Path,
) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    registration = _lifecycle_registration()
    rejected = _lifecycle_transition(
        registration,
        to_state=StrategyLifecycleState.REJECTED,
        decision_session_date=EFFECTIVE_DATE,
        effective_session_date=dt.date(2026, 7, 17),
        decided_at=dt.datetime(2026, 7, 16, 20, tzinfo=dt.UTC),
    )
    invalid_direct_promotion = StrategyLifecycleEvent.model_construct(
        **(
            rejected.model_dump(mode="python")
            | {
                "sequence": 2,
                "from_state": StrategyLifecycleState.EXPERIMENTAL_SHADOW,
                "to_state": StrategyLifecycleState.PAPER_CHAMPION,
                "previous_event_key": strategy_lifecycle_event_key(registration),
            }
        )
    )
    rejected_successor = StrategyLifecycleEvent.model_construct(
        **(
            rejected.model_dump(mode="python")
            | {
                "sequence": 3,
                "from_state": StrategyLifecycleState.REJECTED,
                "to_state": StrategyLifecycleState.EXPERIMENTAL_SHADOW,
                "decision_session_date": dt.date(2026, 7, 17),
                "effective_session_date": dt.date(2026, 7, 20),
                "decided_at": dt.datetime(2026, 7, 17, 20, tzinfo=dt.UTC),
                "previous_event_key": strategy_lifecycle_event_key(rejected),
            }
        )
    )

    with store.writer() as writer:
        assert writer.append_lifecycle_event(registration) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(invalid_direct_promotion)
        assert writer.append_lifecycle_event(rejected) is True
        with pytest.raises(InvalidExperimentLedgerSourceError):
            _ = writer.append_lifecycle_event(rejected_successor)


@pytest.mark.parametrize("corruption", ("payload", "key"))
def test_lifecycle_reader_detects_payload_or_key_corruption(
    tmp_path: Path,
    corruption: str,
) -> None:
    database = tmp_path / "experiment.sqlite3"
    store = ExperimentLedgerStore(database)
    _register_lineage(store)
    with store.writer() as writer:
        assert writer.append_lifecycle_event(_lifecycle_registration()) is True

    with sqlite3.connect(database) as connection:
        _ = connection.execute("DROP TRIGGER strategy_lifecycle_events_no_update")
        if corruption == "payload":
            _ = connection.execute("UPDATE strategy_lifecycle_events SET payload_json = '{}'")
        else:
            _ = connection.execute(
                "UPDATE strategy_lifecycle_events SET event_key = ?",
                ("0" * 64,),
            )
        connection.commit()

    with pytest.raises(InvalidExperimentLedgerSourceError):
        _ = ExperimentLedgerReader(database).lifecycle_events(_version().strategy_version)
