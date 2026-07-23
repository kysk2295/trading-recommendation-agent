from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_multi_market_trial_store import _lineage, _register_lineage
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_schema import (
    CREATE_EXPERIMENT_LEDGER_SCHEMA_V1,
    CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4,
    CREATE_MULTI_MARKET_TRIAL_SCHEMA_V5,
    CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2,
    CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerStore,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.multi_market_experiment_keys import (
    multi_market_hypothesis_registration_key,
    multi_market_strategy_version_registration_key,
)
from trading_agent.multi_market_experiment_store import (
    register_multi_market_hypothesis,
    register_multi_market_strategy_version,
)
from trading_agent.multi_market_lifecycle_keys import multi_market_lifecycle_event_key
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent

KST = ZoneInfo("Asia/Seoul")
CALENDAR_1 = "a" * 64
CALENDAR_2 = "b" * 64
REVIEW_KEY = "c" * 64


def _registration() -> MultiMarketStrategyLifecycleEvent:
    hypothesis, version = _lineage()
    return MultiMarketStrategyLifecycleEvent(
        strategy_version=version.strategy_version,
        strategy_lane=version.strategy_lane,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version="multi_market_lifecycle_v1",
        decision_session_date=dt.date(2026, 7, 19),
        effective_session_date=dt.date(2026, 7, 20),
        decided_at=dt.datetime(2026, 7, 19, 8, 31, tzinfo=KST),
        session_calendar_snapshot_id=CALENDAR_1,
        evidence_keys=tuple(
            sorted(
                (
                    CALENDAR_1,
                    str(multi_market_hypothesis_registration_key(hypothesis)),
                    version.experiment_scope_key,
                    str(multi_market_strategy_version_registration_key(version)),
                )
            )
        ),
        reason_codes=("multi_market_strategy_registered",),
        previous_event_key=None,
    )


def _transition(
    previous: MultiMarketStrategyLifecycleEvent,
    target: StrategyLifecycleState = StrategyLifecycleState.CHALLENGER,
) -> MultiMarketStrategyLifecycleEvent:
    previous_key = str(multi_market_lifecycle_event_key(previous))
    return MultiMarketStrategyLifecycleEvent(
        strategy_version=previous.strategy_version,
        strategy_lane=previous.strategy_lane,
        sequence=previous.sequence + 1,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=previous.to_state,
        to_state=target,
        policy_version="kr_theme_day_lifecycle_v1",
        decision_session_date=dt.date(2026, 7, 20),
        effective_session_date=dt.date(2026, 7, 21),
        decided_at=dt.datetime(2026, 7, 20, 15, 40, tzinfo=KST),
        session_calendar_snapshot_id=CALENDAR_2,
        evidence_keys=tuple(sorted((CALENDAR_2, previous_key, REVIEW_KEY))),
        reason_codes=("minimum_forward_evidence_satisfied", "review_evidence_verified"),
        previous_event_key=previous_key,
    )


def test_multi_market_lifecycle_appends_exact_chain_and_projects_as_of(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    registration = _registration()
    transition = _transition(registration)

    with store.writer() as writer:
        assert writer.append_multi_market_lifecycle_event(registration) is True
        assert writer.append_multi_market_lifecycle_event(registration) is False
        assert writer.append_multi_market_lifecycle_event(transition) is True
        assert writer.append_multi_market_lifecycle_event(transition) is False

    events = store.multi_market_lifecycle_events(registration.strategy_version)
    before = store.multi_market_lifecycle_state(registration.strategy_version, dt.date(2026, 7, 20))
    after = store.multi_market_lifecycle_state(registration.strategy_version, dt.date(2026, 7, 21))
    assert tuple(stored.event for stored in events) == (registration, transition)
    assert before is not None and before.event.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
    assert after is not None and after.event.to_state is StrategyLifecycleState.CHALLENGER


def test_shadow_multi_market_lifecycle_rejects_paper_champion(tmp_path: Path) -> None:
    store = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _register_lineage(store)
    registration = _registration()
    challenger = _transition(registration)
    paper = _transition(challenger, StrategyLifecycleState.PAPER_CHAMPION).model_copy(
        update={
            "decision_session_date": dt.date(2026, 7, 21),
            "effective_session_date": dt.date(2026, 7, 22),
            "decided_at": dt.datetime(2026, 7, 21, 15, 40, tzinfo=KST),
            "previous_event_key": str(multi_market_lifecycle_event_key(challenger)),
            "evidence_keys": tuple(sorted((CALENDAR_2, REVIEW_KEY, str(multi_market_lifecycle_event_key(challenger))))),
        }
    )
    with store.writer() as writer:
        assert writer.append_multi_market_lifecycle_event(registration) is True
        assert writer.append_multi_market_lifecycle_event(challenger) is True
    with pytest.raises(InvalidExperimentLedgerSourceError), store.writer() as writer:
        _ = writer.append_multi_market_lifecycle_event(paper)


def test_writer_migrates_v5_multi_market_rows_without_rewrite(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    hypothesis, version = _lineage()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            CREATE_EXPERIMENT_LEDGER_SCHEMA_V1
            + CREATE_RESEARCH_SOURCE_LINEAGE_SCHEMA_V2
            + CREATE_STRATEGY_AUTHORITY_BINDING_SCHEMA_V3
            + CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4
            + CREATE_MULTI_MARKET_TRIAL_SCHEMA_V5
        )
        _ = connection.execute("PRAGMA user_version = 5")
        assert register_multi_market_hypothesis(connection, hypothesis) is True
        assert register_multi_market_strategy_version(connection, version) is True
        connection.commit()

    with ExperimentLedgerStore(database).writer():
        pass

    assert ExperimentLedgerStore(database).multi_market_strategy_versions()[0].registration == version
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
