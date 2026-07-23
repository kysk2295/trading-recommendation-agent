from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.intraday_research_loop import (
    IntradayResearchLoopError,
    IntradayResearchLoopPaths,
    run_intraday_research_loop,
)
from trading_agent.intraday_research_loop_models import IntradayResearchManifest
from trading_agent.lane_bootstrap import bootstrap_lane_control_plane
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest
from trading_agent.source_backed_intraday_design import (
    InvalidSourceBackedIntradayDesignError,
    register_source_backed_intraday_design,
)
from trading_agent.source_driven_hypothesis_queue import (
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)
from trading_agent.source_driven_hypothesis_queue_models import HypothesisQueueRoute

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_EXAMPLE = PROJECT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
INPUT_CSV = PROJECT / "examples" / "example_intraday.csv"
INPUT_SHA256 = "2a0222a20540d7d07b95130dc6a7414733f75f5210958820fde8021259e96391"


def test_source_backed_intraday_manifest_binds_queue_card_and_new_version() -> None:
    manifest = IntradayResearchManifest.model_validate(
        {
            "schema_version": 2,
            "family": "source_backed_intraday_challengers_v2",
            "code_version": "a" * 40,
            "hypotheses": [
                {
                    "strategy": "vwap_reclaim",
                    "hypothesis_id": "H-MOM-VWAP-SOURCE-002",
                    "strategy_version": "first_vwap_reclaim_source_v2",
                    "queue_card_key": "b" * 64,
                }
            ],
            "source_queue_snapshot_id": "c" * 64,
            "input_sha256": INPUT_SHA256,
            "registered_at": "2026-07-23T02:32:00Z",
            "evaluator_version": "intraday_walk_forward_v1",
            "minimum_training_sessions": 0,
            "max_bars": 10,
            "max_sessions": 1,
            "per_side_fee_bps": 5,
            "per_side_slippage_bps": 15,
            "bootstrap_samples": 200,
            "rss_limit_gib": 9.5,
        }
    )

    selection = manifest.hypotheses[0]
    assert manifest.schema_version == 2
    assert selection.strategy_version == "first_vwap_reclaim_source_v2"
    assert selection.queue_card_key == "b" * 64
    assert manifest.source_queue_snapshot_id == "c" * 64
    assert manifest.input_sha256 == INPUT_SHA256


def test_source_backed_design_registers_exact_new_version_and_replays(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    card_manifest = _source_card_manifest(tmp_path)
    _ = register_research_hypothesis_manifest(card_manifest, ledger)
    queue = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
    card = queue.snapshot.items[0]
    manifest = _research_manifest(queue.snapshot_id, card.card_key)

    first = register_source_backed_intraday_design(manifest, queue, ledger)
    replay = register_source_backed_intraday_design(manifest, queue, ledger)

    versions = ExperimentLedgerReader(ledger.path).strategy_versions()
    assert first.versions_created == 1
    assert replay.versions_created == 0
    assert first.versions_total == replay.versions_total == 1
    assert len(versions) == 1
    assert versions[0].registration.strategy_version == "first_vwap_reclaim_source_v2"
    assert versions[0].registration.hypothesis_id == "H-MOM-VWAP-SOURCE-002"
    assert versions[0].registration.ledger_recorded_at == manifest.registered_at


def test_source_backed_design_rejects_reusing_stale_queue_for_another_version(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = register_research_hypothesis_manifest(_source_card_manifest(tmp_path), ledger)
    queue = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
    manifest = _research_manifest(queue.snapshot_id, queue.snapshot.items[0].card_key)
    _ = register_source_backed_intraday_design(manifest, queue, ledger)
    stale = manifest.model_copy(
        update={
            "hypotheses": (
                manifest.hypotheses[0].model_copy(update={"strategy_version": "first_vwap_reclaim_source_v3"}),
            )
        }
    )

    try:
        _ = register_source_backed_intraday_design(stale, queue, ledger)
    except InvalidSourceBackedIntradayDesignError:
        pass
    else:
        raise AssertionError("stale source queue was reused for a different strategy version")


def test_source_backed_intraday_loop_runs_historical_trial_and_independent_review(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = register_research_hypothesis_manifest(_source_card_manifest(tmp_path), ledger)
    queue = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
    queue_path, _ = publish_source_driven_hypothesis_queue(tmp_path / "queue", queue)
    lane_registry = tmp_path / "lane.sqlite3"
    _ = bootstrap_lane_control_plane(LaneRegistryStore(lane_registry))
    manifest = _research_manifest(queue.snapshot_id, queue.snapshot.items[0].card_key)
    paths = IntradayResearchLoopPaths(
        input_csv=INPUT_CSV,
        lane_registry=lane_registry,
        experiment_ledger=ledger.path,
        artifact_root=tmp_path / "artifacts",
        review_root=tmp_path / "reviews",
        source_queue_artifact=queue_path,
    )

    first = run_intraday_research_loop(manifest, paths)
    replay = run_intraday_research_loop(manifest, paths)

    projected = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
    assert first.trials_total == 1
    assert first.experiment_artifacts_created == 1
    assert first.review_artifacts_created == 1
    assert replay.experiment_artifacts_created == 0
    assert replay.review_artifacts_created == 0
    assert projected.snapshot.items[0].route is HypothesisQueueRoute.INDEPENDENT_REVIEW


def test_source_backed_intraday_loop_rejects_unregistered_input_before_version(tmp_path: Path) -> None:
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = register_research_hypothesis_manifest(_source_card_manifest(tmp_path), ledger)
    queue = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
    queue_path, _ = publish_source_driven_hypothesis_queue(tmp_path / "queue", queue)
    lane_registry = tmp_path / "lane.sqlite3"
    _ = bootstrap_lane_control_plane(LaneRegistryStore(lane_registry))
    manifest = _research_manifest(queue.snapshot_id, queue.snapshot.items[0].card_key).model_copy(
        update={"input_sha256": "d" * 64}
    )

    with pytest.raises(IntradayResearchLoopError):
        _ = run_intraday_research_loop(
            manifest,
            IntradayResearchLoopPaths(
                input_csv=INPUT_CSV,
                lane_registry=lane_registry,
                experiment_ledger=ledger.path,
                artifact_root=tmp_path / "artifacts",
                review_root=tmp_path / "reviews",
                source_queue_artifact=queue_path,
            ),
        )

    assert ExperimentLedgerReader(ledger.path).strategy_versions() == ()


def _research_manifest(snapshot_id: str, card_key: str) -> IntradayResearchManifest:
    return IntradayResearchManifest.model_validate(
        {
            "schema_version": 2,
            "family": "source_backed_intraday_challengers_v2",
            "code_version": "a" * 40,
            "hypotheses": [
                {
                    "strategy": "vwap_reclaim",
                    "hypothesis_id": "H-MOM-VWAP-SOURCE-002",
                    "strategy_version": "first_vwap_reclaim_source_v2",
                    "queue_card_key": card_key,
                }
            ],
            "source_queue_snapshot_id": snapshot_id,
            "input_sha256": INPUT_SHA256,
            "registered_at": "2026-07-23T02:32:00Z",
            "evaluator_version": "intraday_walk_forward_v1",
            "minimum_training_sessions": 0,
            "max_bars": 10,
            "max_sessions": 1,
            "per_side_fee_bps": 5,
            "per_side_slippage_bps": 15,
            "bootstrap_samples": 200,
            "rss_limit_gib": 9.5,
        }
    )


def _source_card_manifest(tmp_path: Path) -> Path:
    payload = json.loads(SOURCE_EXAMPLE.read_text(encoding="utf-8"))
    payload["experiment_scope"] = {
        "schema_version": 1,
        "scope_kind": "single_lane",
        "hypothesis_id": "H-MOM-VWAP-SOURCE-002",
        "primary_lane": "intraday_momentum",
        "lanes": ["intraday_momentum"],
        "registered_at": "2026-07-23T02:30:00Z",
    }
    payload["hypothesis"] = (
        "Eligible high-relative-volume US equities that extend above session VWAP, complete a first pullback, "
        "and reclaim with renewed volume may show positive cost-adjusted same-day continuation."
    )
    payload["falsification_rule"] = (
        "Reject the immutable version when bounded out-of-sample and shadow evidence fail its registered "
        "cost-adjusted comparison and coverage requirements."
    )
    payload["economic_mechanism"] = (
        "Underreaction and delayed participation may create a bounded continuation after renewed demand."
    )
    payload["counterfactual_baseline"] = (
        "Matched eligible sessions without a first-pullback VWAP reclaim under the same data and cost contract."
    )
    payload["ledger_recorded_at"] = "2026-07-23T02:31:00Z"
    path = tmp_path / "source-card.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
