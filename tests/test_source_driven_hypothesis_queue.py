from __future__ import annotations

import datetime as dt
import json
import stat
from pathlib import Path

from trading_agent.experiment_ledger_keys import experiment_trial_event_key
from trading_agent.experiment_ledger_models import (
    ExperimentTrialEvent,
    ExperimentTrialRegistration,
    ResearchHypothesisCard,
    StrategyVersionRegistration,
    TrialEventKind,
    TrialKind,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest
from trading_agent.source_driven_hypothesis_queue import (
    HypothesisQueueRoute,
    load_source_driven_hypothesis_queue,
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"


def test_source_backed_unimplemented_hypothesis_routes_to_strategy_design(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    _ = register_research_hypothesis_manifest(EXAMPLE, ExperimentLedgerStore(database))

    artifact = project_source_driven_hypothesis_queue(ExperimentLedgerReader(database))

    assert len(artifact.snapshot.items) == 1
    item = artifact.snapshot.items[0]
    assert item.route is HypothesisQueueRoute.STRATEGY_DESIGN
    assert item.strategy_versions == ()
    assert item.historical_trial_ids == ()
    assert artifact.snapshot.lifecycle_authority is False
    assert artifact.snapshot.allocation_authority is False
    assert artifact.snapshot.order_authority is False


def test_discovery_only_hypothesis_routes_to_evidence_review(tmp_path: Path) -> None:
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    source = payload["research_sources"][0]
    source["source_id"] = "github-public-strategy"
    source["source_kind"] = "open_source_repository"
    source["source_url"] = "https://github.com/example/public-strategy"
    payload["research_sources"] = [source]
    payload["research_source_ids"] = [source["source_id"]]
    manifest = tmp_path / "discovery.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    database = tmp_path / "experiment.sqlite3"
    _ = register_research_hypothesis_manifest(manifest, ExperimentLedgerStore(database))

    artifact = project_source_driven_hypothesis_queue(ExperimentLedgerReader(database))

    assert artifact.snapshot.items[0].route is HypothesisQueueRoute.EVIDENCE_REVIEW


def test_registered_strategy_routes_to_historical_replay_and_artifact_replays(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    ledger = ExperimentLedgerStore(database)
    _ = register_research_hypothesis_manifest(EXAMPLE, ledger)
    reader = ExperimentLedgerReader(database)
    card = reader.research_hypothesis_cards()[0].card
    version = StrategyVersionRegistration(
        strategy_id="swing_new_high_rvol",
        strategy_version="swing-new-high-rvol-v1",
        hypothesis_id=card.hypothesis.hypothesis_id,
        experiment_scope_key=card.hypothesis.experiment_scope_key,
        lane_id=card.hypothesis.primary_lane,
        code_version="a" * 40,
        parameter_set=("lookback_sessions:20", "minimum_relative_volume:1.5"),
        data_contract=("daily_adjusted_bars_v1",),
        cost_model=("round_trip_bps:40",),
        portfolio_policy=("equal_weight",),
        source_registered_at=card.hypothesis.source_registered_at,
        ledger_recorded_at=card.hypothesis.ledger_recorded_at,
    )
    with ledger.writer() as writer:
        assert writer.register_strategy_version(version)

    artifact = project_source_driven_hypothesis_queue(reader)
    path, created = publish_source_driven_hypothesis_queue(tmp_path / "queue", artifact)
    replay_path, replay_created = publish_source_driven_hypothesis_queue(tmp_path / "queue", artifact)

    assert artifact.snapshot.items[0].route is HypothesisQueueRoute.HISTORICAL_REPLAY
    assert artifact.snapshot.items[0].strategy_versions == (version.strategy_version,)
    assert created is True
    assert replay_created is False
    assert replay_path == path
    assert load_source_driven_hypothesis_queue(path) == artifact
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_completed_historical_trial_routes_to_independent_review(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    ledger = ExperimentLedgerStore(database)
    _ = register_research_hypothesis_manifest(EXAMPLE, ledger)
    reader = ExperimentLedgerReader(database)
    card = reader.research_hypothesis_cards()[0].card
    version = _strategy_version(card)
    trial = ExperimentTrialRegistration(
        trial_id="swing-new-high-rvol-historical-v1",
        strategy_version=version.strategy_version,
        trial_kind=TrialKind.HISTORICAL_REPLAY,
        experiment_scope=card.hypothesis.experiment_scope,
        experiment_scope_key=card.hypothesis.experiment_scope_key,
        evaluator_version="purged_walk_forward_v1",
        data_version="b" * 64,
        feed_entitlement="alpaca_sip_historical_read_only",
        planned_start=dt.date(2026, 7, 17),
        planned_end=dt.date(2026, 7, 17),
        registered_at=card.hypothesis.ledger_recorded_at,
        evidence_budget=("maximum_sessions:60", "minimum_oos_sessions:20"),
    )
    started = ExperimentTrialEvent(
        trial_id=trial.trial_id,
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=dt.datetime(2026, 7, 17, 13, 31, tzinfo=dt.UTC),
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )
    completed = ExperimentTrialEvent(
        trial_id=trial.trial_id,
        sequence=2,
        event_kind=TrialEventKind.COMPLETED,
        occurred_at=dt.datetime(2026, 7, 17, 14, tzinfo=dt.UTC),
        artifact_sha256s=("c" * 64,),
        reason_codes=(),
        previous_event_key=str(experiment_trial_event_key(started)),
    )
    with ledger.writer() as writer:
        assert writer.register_strategy_version(version)
        assert writer.register_trial(trial)
        assert writer.append_trial_event(started)
        assert writer.append_trial_event(completed)

    artifact = project_source_driven_hypothesis_queue(reader)

    assert artifact.snapshot.items[0].route is HypothesisQueueRoute.INDEPENDENT_REVIEW


def test_new_strategy_version_does_not_reuse_prior_version_completed_trial(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    ledger = ExperimentLedgerStore(database)
    _ = register_research_hypothesis_manifest(EXAMPLE, ledger)
    reader = ExperimentLedgerReader(database)
    card = reader.research_hypothesis_cards()[0].card
    first_version = _strategy_version(card)
    _register_completed_trial(ledger, card, first_version)
    second_version = StrategyVersionRegistration.model_validate(
        first_version.model_dump(mode="python")
        | {
            "strategy_version": "swing-new-high-rvol-v2",
            "code_version": "d" * 40,
            "ledger_recorded_at": first_version.ledger_recorded_at + dt.timedelta(minutes=1),
        }
    )
    with ledger.writer() as writer:
        assert writer.register_strategy_version(second_version)

    artifact = project_source_driven_hypothesis_queue(reader)

    item = artifact.snapshot.items[0]
    assert item.route is HypothesisQueueRoute.HISTORICAL_REPLAY
    assert item.strategy_versions == (first_version.strategy_version, second_version.strategy_version)
    assert item.historical_trial_ids == ()


def _strategy_version(card: ResearchHypothesisCard) -> StrategyVersionRegistration:
    hypothesis = card.hypothesis
    return StrategyVersionRegistration(
        strategy_id="swing_new_high_rvol",
        strategy_version="swing-new-high-rvol-v1",
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_scope_key=hypothesis.experiment_scope_key,
        lane_id=hypothesis.primary_lane,
        code_version="a" * 40,
        parameter_set=("lookback_sessions:20", "minimum_relative_volume:1.5"),
        data_contract=("daily_adjusted_bars_v1",),
        cost_model=("round_trip_bps:40",),
        portfolio_policy=("equal_weight",),
        source_registered_at=hypothesis.source_registered_at,
        ledger_recorded_at=hypothesis.ledger_recorded_at,
    )


def _register_completed_trial(
    ledger: ExperimentLedgerStore,
    card: ResearchHypothesisCard,
    version: StrategyVersionRegistration,
) -> None:
    trial = ExperimentTrialRegistration(
        trial_id="swing-new-high-rvol-historical-v1",
        strategy_version=version.strategy_version,
        trial_kind=TrialKind.HISTORICAL_REPLAY,
        experiment_scope=card.hypothesis.experiment_scope,
        experiment_scope_key=card.hypothesis.experiment_scope_key,
        evaluator_version="purged_walk_forward_v1",
        data_version="b" * 64,
        feed_entitlement="alpaca_sip_historical_read_only",
        planned_start=dt.date(2026, 7, 17),
        planned_end=dt.date(2026, 7, 17),
        registered_at=card.hypothesis.ledger_recorded_at,
        evidence_budget=("maximum_sessions:60", "minimum_oos_sessions:20"),
    )
    started = ExperimentTrialEvent(
        trial_id=trial.trial_id,
        sequence=1,
        event_kind=TrialEventKind.STARTED,
        occurred_at=dt.datetime(2026, 7, 17, 13, 31, tzinfo=dt.UTC),
        artifact_sha256s=(),
        reason_codes=(),
        previous_event_key=None,
    )
    completed = ExperimentTrialEvent(
        trial_id=trial.trial_id,
        sequence=2,
        event_kind=TrialEventKind.COMPLETED,
        occurred_at=dt.datetime(2026, 7, 17, 14, tzinfo=dt.UTC),
        artifact_sha256s=("c" * 64,),
        reason_codes=(),
        previous_event_key=str(experiment_trial_event_key(started)),
    )
    with ledger.writer() as writer:
        assert writer.register_strategy_version(version)
        assert writer.register_trial(trial)
        assert writer.append_trial_event(started)
        assert writer.append_trial_event(completed)
