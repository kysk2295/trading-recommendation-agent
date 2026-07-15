from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from tests.daily_research_fixtures import write_complete_session
from trading_agent.adaptive_evaluation_models import AdaptiveAction, AdaptiveEvaluation
from trading_agent.daily_research_contract import EVALUATOR_VERSION, FEED_ENTITLEMENT, strategy_contract
from trading_agent.daily_research_ledger import build_daily_record, write_daily_record
from trading_agent.daily_research_models import DailyResearchRecord
from trading_agent.experiment_ledger_bootstrap import bootstrap_current_intraday_experiments
from trading_agent.experiment_ledger_keys import experiment_trial_event_key, strategy_lifecycle_event_key
from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEvent,
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.lane_contract_keys import lane_daily_snapshot_key, lane_manifest_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
    INTRADAY_MANIFEST,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryReader, LaneRegistryStore
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lane_review_store import LaneReviewReader, LaneReviewStore
from trading_agent.lane_reviewer import review_intraday_lane_day
from trading_agent.orb_forward_trial import (
    InvalidOrbForwardTrialSourceError,
    finalize_orb_shadow_trial,
    orb_shadow_trial_data_version,
    orb_shadow_trial_id,
    register_orb_shadow_trial,
    start_orb_shadow_trial,
)
from trading_agent.strategy_factory import StrategyMode

SESSION_DATE = dt.date(2026, 7, 16)
NEXT_SESSION_DATE = dt.date(2026, 7, 17)
BOOTSTRAP_AT = dt.datetime(2026, 7, 15, 20, 0, tzinfo=dt.UTC)
PREOPEN = dt.datetime(2026, 7, 16, 12, 0, tzinfo=dt.UTC)
OPEN = dt.datetime(2026, 7, 16, 13, 30, tzinfo=dt.UTC)
AFTER_CLOSE = dt.datetime(2026, 7, 16, 20, 30, tzinfo=dt.UTC)
FINALIZED_AT = dt.datetime(2026, 7, 16, 20, 5, tzinfo=dt.UTC)
REVIEWED_AT = dt.datetime(2026, 7, 16, 20, 15, tzinfo=dt.UTC)
NEXT_PREOPEN = dt.datetime(2026, 7, 17, 12, 0, tzinfo=dt.UTC)
CODE_VERSION = "test-code"
ORB_CONTRACT = strategy_contract(StrategyMode.ORB)
EXPECTED_EVIDENCE_BUDGET = (
    "adaptive_evaluation:1",
    "daily_research_record:1",
    "lane_daily_snapshot:1",
    "lane_review_event:1",
)


@dataclass(frozen=True, slots=True)
class _TerminalSources:
    registry: LaneRegistryStore
    reviews: LaneReviewStore
    experiments: ExperimentLedgerStore
    session: Path
    record: DailyResearchRecord
    snapshot: LaneDailySnapshot


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


def test_started_event_is_regular_session_only_and_replays_exactly(tmp_path: Path) -> None:
    registry, experiments = _seed_lineage(tmp_path)
    registration = register_orb_shadow_trial(
        lane_registry=registry,
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        runtime_code_version=CODE_VERSION,
        registered_at=PREOPEN,
    ).registration

    first = start_orb_shadow_trial(
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        started_at=OPEN + dt.timedelta(minutes=1),
    )
    replay = start_orb_shadow_trial(
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        started_at=OPEN + dt.timedelta(minutes=5),
    )

    assert first.created is True
    assert first.event.trial_id == registration.trial_id
    assert first.event.sequence == 1
    assert first.event.event_kind is TrialEventKind.STARTED
    assert first.event.occurred_at == OPEN + dt.timedelta(minutes=1)
    assert first.event.artifact_sha256s == ()
    assert first.event.reason_codes == ()
    assert first.event.previous_event_key is None
    assert replay.created is False
    assert replay.event == first.event
    assert tuple(stored.event for stored in experiments.trial_events(registration.trial_id)) == (first.event,)


@pytest.mark.parametrize(
    "started_at",
    (
        OPEN - dt.timedelta(microseconds=1),
        dt.datetime(2026, 7, 16, 20, 0, tzinfo=dt.UTC),
    ),
)
def test_started_event_rejects_time_outside_regular_session(
    tmp_path: Path,
    started_at: dt.datetime,
) -> None:
    registry, experiments = _seed_lineage(tmp_path)
    registration = register_orb_shadow_trial(
        lane_registry=registry,
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        runtime_code_version=CODE_VERSION,
        registered_at=PREOPEN,
    ).registration

    with pytest.raises(InvalidOrbForwardTrialSourceError):
        _ = start_orb_shadow_trial(
            experiment_ledger=experiments,
            session_date=SESSION_DATE,
            started_at=started_at,
        )

    assert experiments.trial_events(registration.trial_id) == ()


def test_completed_terminal_binds_four_exact_artifacts_and_replays(tmp_path: Path) -> None:
    sources = _seed_terminal_sources(tmp_path)
    started = sources.experiments.trial_events(orb_shadow_trial_id(SESSION_DATE, ORB_CONTRACT.strategy_version))[0]

    first = finalize_orb_shadow_trial(
        experiment_ledger=sources.experiments,
        lane_registry=LaneRegistryReader(sources.registry.path),
        review_ledger=LaneReviewReader(sources.reviews.path),
        session=sources.session,
        session_date=SESSION_DATE,
        occurred_at=AFTER_CLOSE,
    )
    replay = finalize_orb_shadow_trial(
        experiment_ledger=sources.experiments,
        lane_registry=LaneRegistryReader(sources.registry.path),
        review_ledger=LaneReviewReader(sources.reviews.path),
        session=sources.session,
        session_date=SESSION_DATE,
        occurred_at=AFTER_CLOSE + dt.timedelta(minutes=5),
    )

    expected_artifacts = tuple(
        sorted(
            (
                _daily_record_sha256(sources.session),
                _adaptive_sha256(sources.session),
                str(lane_daily_snapshot_key(sources.snapshot)),
                str(lane_review_event_key(sources.reviews.events()[0].event)),
            )
        )
    )
    assert first.created is True
    assert first.event.sequence == 2
    assert first.event.event_kind is TrialEventKind.COMPLETED
    assert first.event.occurred_at == AFTER_CLOSE
    assert first.event.artifact_sha256s == expected_artifacts
    assert first.event.reason_codes == ()
    assert first.event.previous_event_key == experiment_trial_event_key(started.event)
    assert replay.created is False
    assert replay.event == first.event
    assert len(sources.experiments.trial_events(first.event.trial_id)) == 2
    with pytest.raises(InvalidOrbForwardTrialSourceError):
        _ = start_orb_shadow_trial(
            experiment_ledger=sources.experiments,
            session_date=SESSION_DATE,
            started_at=OPEN + dt.timedelta(minutes=10),
        )


def test_incomplete_exact_evidence_is_censored_instead_of_zero_return(
    tmp_path: Path,
) -> None:
    sources = _seed_terminal_sources(tmp_path, censored=True)

    result = finalize_orb_shadow_trial(
        experiment_ledger=sources.experiments,
        lane_registry=sources.registry,
        review_ledger=sources.reviews,
        session=sources.session,
        session_date=SESSION_DATE,
        occurred_at=AFTER_CLOSE,
    )

    assert result.created is True
    assert result.event.event_kind is TrialEventKind.CENSORED
    assert result.event.reason_codes == (
        "daily_incidents_present",
        "forward_day_ineligible",
        "snapshot_data_quality_incomplete",
        "snapshot_incidents_present",
    )
    assert len(result.event.artifact_sha256s) == 4


def test_terminal_requires_a_started_event_and_post_close_time(tmp_path: Path) -> None:
    registry, experiments = _seed_lineage(tmp_path)
    registration = register_orb_shadow_trial(
        lane_registry=registry,
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        runtime_code_version=CODE_VERSION,
        registered_at=PREOPEN,
    ).registration

    with pytest.raises(InvalidOrbForwardTrialSourceError):
        _ = finalize_orb_shadow_trial(
            experiment_ledger=experiments,
            lane_registry=registry,
            review_ledger=LaneReviewReader(tmp_path / "missing-review.sqlite3"),
            session=tmp_path / "missing-session",
            session_date=SESSION_DATE,
            occurred_at=OPEN + dt.timedelta(minutes=30),
        )

    assert experiments.trial_events(registration.trial_id) == ()


@pytest.mark.parametrize(
    "record_change",
    (
        "code_version",
        "feed_entitlement",
        "parameter_set",
        "cost_model",
        "portfolio_policy",
        "data_version",
    ),
)
def test_terminal_rejects_daily_contract_mismatch_without_event(
    tmp_path: Path,
    record_change: str,
) -> None:
    sources = _seed_terminal_sources(tmp_path, record_change=record_change)

    with pytest.raises(InvalidOrbForwardTrialSourceError):
        _ = finalize_orb_shadow_trial(
            experiment_ledger=sources.experiments,
            lane_registry=sources.registry,
            review_ledger=sources.reviews,
            session=sources.session,
            session_date=SESSION_DATE,
            occurred_at=AFTER_CLOSE,
        )

    trial_id = orb_shadow_trial_id(SESSION_DATE, ORB_CONTRACT.strategy_version)
    assert len(sources.experiments.trial_events(trial_id)) == 1


@pytest.mark.parametrize("tamper", ("artifact", "adaptive", "parent_ledger"))
def test_terminal_rejects_changed_evidence_bytes_without_event(
    tmp_path: Path,
    tamper: str,
) -> None:
    sources = _seed_terminal_sources(tmp_path)
    if tamper == "artifact":
        path = sources.session / "market_risk_screen.csv"
    elif tamper == "adaptive":
        path = sources.session / "adaptive_evaluation" / "adaptive_evaluation.json"
    else:
        path = sources.session.parent / "daily_research_ledger.jsonl"
        path.write_text("", encoding="utf-8")
    if tamper != "parent_ledger":
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(InvalidOrbForwardTrialSourceError):
        _ = finalize_orb_shadow_trial(
            experiment_ledger=sources.experiments,
            lane_registry=sources.registry,
            review_ledger=sources.reviews,
            session=sources.session,
            session_date=SESSION_DATE,
            occurred_at=AFTER_CLOSE,
        )

    trial_id = orb_shadow_trial_id(SESSION_DATE, ORB_CONTRACT.strategy_version)
    assert len(sources.experiments.trial_events(trial_id)) == 1


def _seed_terminal_sources(
    tmp_path: Path,
    *,
    censored: bool = False,
    record_change: str | None = None,
) -> _TerminalSources:
    registry, experiments = _seed_lineage(tmp_path)
    _ = register_orb_shadow_trial(
        lane_registry=registry,
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        runtime_code_version=CODE_VERSION,
        registered_at=PREOPEN,
    )
    _ = start_orb_shadow_trial(
        experiment_ledger=experiments,
        session_date=SESSION_DATE,
        started_at=OPEN + dt.timedelta(minutes=1),
    )
    session = tmp_path / "live_sessions" / "20260716"
    write_complete_session(session, SESSION_DATE)
    if not censored:
        (session / "kis_read_retry_cycles.csv").write_text(
            "started_at,retry_count,recovered_count,repeated_failure_count\n2026-07-16T10:00:00-04:00,0,0,0\n",
            encoding="utf-8",
        )
        (session / "kis_read_retry_events.csv").unlink()
    record = build_daily_record(
        session,
        SESSION_DATE,
        StrategyMode.ORB,
        CODE_VERSION,
        FINALIZED_AT - dt.timedelta(minutes=3),
    )
    if record_change is not None:
        record = _changed_record(record, record_change)
    if censored:
        record = record.model_copy(
            update={
                "session_quality": record.session_quality.model_copy(update={"forward_day_eligible": False}),
                "incidents": ("fixture_daily_incident",),
            }
        )
    assert write_daily_record(session, record) is True

    snapshot = LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=SESSION_DATE,
        finalized_at=FINALIZED_AT,
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(record.experiment_scope_key,),
        source_ledger_generation=1,
        source_ledger_sha256="f" * 64,
        champion_strategy_versions=(),
        data_quality_complete=not censored,
        allocation_eligible=False,
        incidents=("fixture_snapshot_incident",) if censored else (),
        conservative_equity=Decimal("30000"),
        realized_pnl=Decimal(0),
        unrealized_pnl=Decimal(0),
        planned_open_risk=Decimal(0),
        open_order_count=0,
        open_position_count=0,
    )
    with registry.writer() as writer:
        assert writer.append_daily_snapshot(snapshot) is True

    adaptive = AdaptiveEvaluation(
        schema_version=1,
        as_of=SESSION_DATE,
        strategy_version=record.strategy_version,
        evaluator_version=record.evaluator_version,
        action=AdaptiveAction.COLLECTING,
        reasons=("minimum_five_day_observation_pending",),
        windows=(),
        regime_coverage=0.0,
        regimes=(),
        feature_coverage=0.0,
        gap_feature_coverage=0.0,
        cohorts=(),
        proof_blockers=("broker_paper_ledger_missing",),
        automatic_state_change_allowed=False,
    )
    adaptive_path = session / "adaptive_evaluation" / "adaptive_evaluation.json"
    adaptive_path.parent.mkdir(parents=True)
    adaptive_path.write_text(adaptive.model_dump_json(indent=2) + "\n", encoding="utf-8")
    reviews = LaneReviewStore(tmp_path / "review.sqlite3")
    _ = review_intraday_lane_day(
        LaneRegistryReader(registry.path),
        reviews,
        session,
        SESSION_DATE,
        reviewed_at=REVIEWED_AT,
    )
    return _TerminalSources(registry, reviews, experiments, session, record, snapshot)


def _daily_record_sha256(session: Path) -> str:
    path = next((session / "daily_research_records").glob("*.json"))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _adaptive_sha256(session: Path) -> str:
    path = session / "adaptive_evaluation" / "adaptive_evaluation.json"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _changed_record(record: DailyResearchRecord, field: str) -> DailyResearchRecord:
    changes: dict[str, object] = {
        "code_version": "different-code",
        "feed_entitlement": "different-feed",
        "parameter_set": (*record.parameter_set, "changed=true"),
        "cost_model": (*record.cost_model, "changed=true"),
        "portfolio_policy": (*record.portfolio_policy, "changed=true"),
        "data_version": "0" * 64,
    }
    return record.model_copy(update={field: changes[field]})


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
