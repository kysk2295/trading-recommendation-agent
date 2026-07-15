from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from trading_agent.daily_research_contract import EVALUATOR_VERSION, FEED_ENTITLEMENT, strategy_contract
from trading_agent.experiment_ledger_bootstrap import bootstrap_current_intraday_experiments
from trading_agent.experiment_ledger_keys import strategy_lifecycle_event_key
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    TrialKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.lane_defaults import CURRENT_INTRADAY_EXPERIMENT_SCOPES, DEFAULT_LANE_MANIFESTS
from trading_agent.lane_registry_store import LaneRegistryReader, LaneRegistryStore
from trading_agent.orb_forward_trial import (
    InvalidOrbForwardTrialSourceError,
    orb_shadow_trial_data_version,
    orb_shadow_trial_id,
    register_orb_shadow_trial,
)
from trading_agent.strategy_factory import StrategyMode

SESSION_DATE = dt.date(2026, 7, 16)
NEXT_SESSION_DATE = dt.date(2026, 7, 17)
BOOTSTRAP_AT = dt.datetime(2026, 7, 15, 20, 0, tzinfo=dt.UTC)
PREOPEN = dt.datetime(2026, 7, 16, 12, 0, tzinfo=dt.UTC)
OPEN = dt.datetime(2026, 7, 16, 13, 30, tzinfo=dt.UTC)
AFTER_CLOSE = dt.datetime(2026, 7, 16, 20, 30, tzinfo=dt.UTC)
NEXT_PREOPEN = dt.datetime(2026, 7, 17, 12, 0, tzinfo=dt.UTC)
CODE_VERSION = "test-code"
ORB_CONTRACT = strategy_contract(StrategyMode.ORB)
EXPECTED_EVIDENCE_BUDGET = (
    "adaptive_evaluation:1",
    "daily_research_record:1",
    "lane_daily_snapshot:1",
    "lane_review_event:1",
)


def test_daily_trial_identity_and_prospective_data_version_are_deterministic() -> None:
    trial_id = orb_shadow_trial_id(SESSION_DATE, ORB_CONTRACT.strategy_version)
    data_version = orb_shadow_trial_data_version()

    assert trial_id.startswith("orb-shadow-20260716-")
    assert re.fullmatch(r"orb-shadow-20260716-[0-9a-f]{12}", trial_id)
    assert trial_id == orb_shadow_trial_id(SESSION_DATE, ORB_CONTRACT.strategy_version)
    assert re.fullmatch(r"[0-9a-f]{64}", data_version)
    assert data_version == orb_shadow_trial_data_version()


def test_preregistration_creates_one_exact_daily_shadow_trial(tmp_path: Path) -> None:
    registry, experiments = _seed_lineage(tmp_path)

    result = register_orb_shadow_trial(
        lane_registry=LaneRegistryReader(registry.path),
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        runtime_code_version=CODE_VERSION,
        registered_at=PREOPEN,
    )

    registration = result.registration
    assert result.created is True
    assert registration.trial_id == orb_shadow_trial_id(SESSION_DATE, ORB_CONTRACT.strategy_version)
    assert registration.strategy_version == ORB_CONTRACT.strategy_version
    assert registration.trial_kind is TrialKind.SHADOW_FORWARD
    assert registration.experiment_scope == ORB_CONTRACT.experiment_scope
    assert registration.evaluator_version == EVALUATOR_VERSION
    assert registration.data_version == orb_shadow_trial_data_version()
    assert registration.feed_entitlement == FEED_ENTITLEMENT
    assert registration.planned_start == SESSION_DATE
    assert registration.planned_end == SESSION_DATE
    assert registration.registered_at == PREOPEN
    assert registration.evidence_budget == EXPECTED_EVIDENCE_BUDGET
    assert tuple(stored.registration for stored in experiments.trials()) == (registration,)
    assert experiments.trial_events(registration.trial_id) == ()


def test_post_open_registration_replays_only_an_existing_exact_trial(tmp_path: Path) -> None:
    registry, experiments = _seed_lineage(tmp_path)
    first = register_orb_shadow_trial(
        lane_registry=registry,
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        runtime_code_version=CODE_VERSION,
        registered_at=PREOPEN,
    )

    replay = register_orb_shadow_trial(
        lane_registry=registry,
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        runtime_code_version=CODE_VERSION,
        registered_at=OPEN + dt.timedelta(minutes=1),
    )

    assert replay.created is False
    assert replay.registration == first.registration
    assert replay.registration.registered_at == PREOPEN
    assert len(experiments.trials()) == 1


def test_new_registration_at_or_after_open_is_rejected(tmp_path: Path) -> None:
    registry, experiments = _seed_lineage(tmp_path)

    with pytest.raises(InvalidOrbForwardTrialSourceError):
        _ = register_orb_shadow_trial(
            lane_registry=registry,
            experiment_ledger=experiments,
            session_date=SESSION_DATE,
            runtime_code_version=CODE_VERSION,
            registered_at=OPEN,
        )

    assert experiments.trials() == ()


@pytest.mark.parametrize("case", ("runtime_code", "lane_source"))
def test_preregistration_rejects_noncanonical_source_without_trial(
    tmp_path: Path,
    case: str,
) -> None:
    registry, experiments = _seed_lineage(tmp_path)
    source: LaneRegistryReader = registry
    runtime_code = CODE_VERSION
    if case == "runtime_code":
        runtime_code = "different-code"
    else:
        source = LaneRegistryReader(tmp_path / "missing-lane.sqlite3")

    with pytest.raises(InvalidOrbForwardTrialSourceError):
        _ = register_orb_shadow_trial(
            lane_registry=source,
            experiment_ledger=experiments,
            session_date=SESSION_DATE,
            runtime_code_version=runtime_code,
            registered_at=PREOPEN,
        )

    assert experiments.trials() == ()


def test_rejected_strategy_version_cannot_register_another_daily_trial(tmp_path: Path) -> None:
    registry, experiments = _seed_lineage(tmp_path)
    previous = experiments.lifecycle_events(ORB_CONTRACT.strategy_version)[-1]
    rejection = StrategyLifecycleEvent(
        strategy_version=ORB_CONTRACT.strategy_version,
        sequence=2,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        to_state=StrategyLifecycleState.REJECTED,
        policy_version="test_rejection_policy",
        decision_session_date=SESSION_DATE,
        effective_session_date=NEXT_SESSION_DATE,
        decided_at=AFTER_CLOSE,
        evidence_keys=("f" * 64,),
        reason_codes=("test_rejection",),
        previous_event_key=strategy_lifecycle_event_key(previous.event),
    )
    with experiments.writer() as writer:
        assert writer.append_lifecycle_event(rejection) is True

    with pytest.raises(InvalidOrbForwardTrialSourceError):
        _ = register_orb_shadow_trial(
            lane_registry=registry,
            experiment_ledger=experiments,
            session_date=NEXT_SESSION_DATE,
            runtime_code_version=CODE_VERSION,
            registered_at=NEXT_PREOPEN,
        )

    assert experiments.trials() == ()


def _seed_lineage(tmp_path: Path) -> tuple[LaneRegistryStore, ExperimentLedgerStore]:
    registry = LaneRegistryStore(tmp_path / "lane.sqlite3")
    with registry.writer() as writer:
        for manifest in DEFAULT_LANE_MANIFESTS:
            assert writer.register_manifest(manifest) is True
        for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES:
            assert writer.register_experiment_scope(scope) is True
    experiments = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    result = bootstrap_current_intraday_experiments(
        lane_registry=registry,
        experiment_ledger=experiments,
        code_version=CODE_VERSION,
        recorded_at=BOOTSTRAP_AT,
    )
    assert result.effective_session_date == SESSION_DATE
    return registry, experiments
