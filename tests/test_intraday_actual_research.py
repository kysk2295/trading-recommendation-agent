from __future__ import annotations

import datetime as dt
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.challenger_replay_fixtures import write_closed_source_session
from tests.intraday_research_input_binding_fixtures import NOW, write_entitlement
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.intraday_actual_research import run_intraday_actual_research
from trading_agent.intraday_actual_research_models import (
    IntradayActualResearchPaths,
    IntradayActualResearchRequest,
)
from trading_agent.intraday_research_dataset_catalog_models import (
    IntradayResearchDatasetCatalogError,
)
from trading_agent.intraday_research_input_binding_models import IntradayResearchStrategyBinding
from trading_agent.lane_bootstrap import bootstrap_lane_control_plane
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest
from trading_agent.source_backed_intraday_design import (
    InvalidSourceBackedIntradayDesignError,
)
from trading_agent.source_driven_hypothesis_queue import (
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)
from trading_agent.strategy_factory import StrategyMode

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"
SCRIPT = PROJECT / "run_intraday_actual_research.py"


def test_actual_research_runs_and_replays_catalog_binding_trial_and_review(tmp_path: Path) -> None:
    request, ledger = _request(tmp_path)

    first = run_intraday_actual_research(request)
    replay = run_intraday_actual_research(request)

    assert first.catalog.dataset.session_count == 1
    assert first.binding.input_sha256 == first.catalog.dataset.input_sha256
    assert first.loop.trials_total == 1
    assert first.loop.experiment_artifacts_created == 1
    assert first.loop.review_artifacts_created == 1
    assert tuple(item.value for item in first.loop.decisions) == ("hold",)
    assert replay.catalog.created is False
    assert replay.binding.created is False
    assert replay.loop.experiment_artifacts_created == 0
    assert replay.loop.review_artifacts_created == 0
    assert len(ExperimentLedgerReader(ledger.path).trials()) == 1
    published = (
        first.catalog.dataset.csv_path,
        first.catalog.dataset.receipt_path,
        first.catalog.catalog_receipt_path,
        *first.binding.foundation_paths,
        first.binding.manifest_path,
        first.binding.receipt_path,
        *tuple(request.paths.artifact_root.glob("*.json")),
        *tuple(request.paths.review_root.glob("*.json")),
    )
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in published)


def test_actual_research_blocks_before_strategy_or_trial_when_current_session_is_not_clean(
    tmp_path: Path,
) -> None:
    request, ledger = _request(tmp_path, current_clean=False)

    with pytest.raises(IntradayResearchDatasetCatalogError):
        _ = run_intraday_actual_research(request)

    reader = ExperimentLedgerReader(ledger.path)
    assert reader.strategy_versions() == ()
    assert reader.trials() == ()
    assert not request.paths.dataset_root.exists()
    assert not request.paths.binding_root.exists()


def test_actual_research_refreshes_same_frozen_version_with_new_clean_data(
    tmp_path: Path,
) -> None:
    first_request, ledger = _request(tmp_path)
    first = run_intraday_actual_research(first_request)
    second_source = tmp_path / "2026-07-15"
    write_closed_source_session(second_source, session_date=dt.date(2026, 7, 15))
    refreshed_queue = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
    refreshed_queue_path, _ = publish_source_driven_hypothesis_queue(
        tmp_path / "queue",
        refreshed_queue,
    )
    with pytest.raises(InvalidSourceBackedIntradayDesignError):
        _ = run_intraday_actual_research(
            replace(
                first_request,
                registered_at=NOW + dt.timedelta(days=1),
                observed_at=NOW + dt.timedelta(days=1),
                paths=replace(
                    first_request.paths,
                    source_queue_artifact=refreshed_queue_path,
                ),
            )
        )
    assert len(ExperimentLedgerReader(ledger.path).trials()) == 1
    second_request = replace(
        first_request,
        session_dirs=(*first_request.session_dirs, second_source),
        required_session_dates=(dt.date(2026, 7, 15),),
        registered_at=NOW + dt.timedelta(days=1),
        observed_at=NOW + dt.timedelta(days=1),
        max_bars=1_000,
        paths=replace(
            first_request.paths,
            source_queue_artifact=refreshed_queue_path,
        ),
    )

    second = run_intraday_actual_research(second_request)
    replay = run_intraday_actual_research(second_request)

    reader = ExperimentLedgerReader(ledger.path)
    assert first.catalog.dataset.session_count == 1
    assert second.catalog.dataset.session_count == 2
    assert second.binding.input_sha256 != first.binding.input_sha256
    assert second.loop.experiment_artifacts_created == 1
    assert second.loop.review_artifacts_created == 1
    assert replay.loop.experiment_artifacts_created == 0
    assert replay.loop.review_artifacts_created == 0
    assert len(reader.strategy_versions()) == 1
    assert len(reader.trials()) == 2


def test_actual_research_cli_exposes_full_vertical_and_rejects_bad_binding() -> None:
    help_result = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    bad_result = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--strategy-binding",
            "invalid",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert help_result.returncode == 0
    assert "--required-session-date" in help_result.stdout
    assert "--entitlement-contract" in help_result.stdout
    assert "--source-queue-artifact" in help_result.stdout
    assert "--strategy-binding" in help_result.stdout
    assert "--lane-registry" in help_result.stdout
    assert "--experiment-ledger" in help_result.stdout
    assert bad_result.returncode == 2


def _request(
    tmp_path: Path,
    *,
    current_clean: bool = True,
) -> tuple[IntradayActualResearchRequest, ExperimentLedgerStore]:
    source = tmp_path / "2026-07-14"
    write_closed_source_session(source, session_date=dt.date(2026, 7, 14))
    session_dirs = (source,)
    required_dates = (dt.date(2026, 7, 14),)
    if not current_clean:
        current = tmp_path / "2026-07-15"
        write_closed_source_session(
            current,
            post_session_complete=False,
            session_date=dt.date(2026, 7, 15),
        )
        session_dirs = (source, current)
        required_dates = (dt.date(2026, 7, 15),)
    ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
    _ = register_research_hypothesis_manifest(SOURCE_MANIFEST, ledger)
    queue = project_source_driven_hypothesis_queue(ExperimentLedgerReader(ledger.path))
    queue_path, _ = publish_source_driven_hypothesis_queue(tmp_path / "queue", queue)
    lane_registry = tmp_path / "lane.sqlite3"
    _ = bootstrap_lane_control_plane(LaneRegistryStore(lane_registry))
    return (
        IntradayActualResearchRequest(
            session_dirs=session_dirs,
            required_session_dates=required_dates,
            strategy_bindings=(
                IntradayResearchStrategyBinding(
                    strategy=StrategyMode.VWAP_RECLAIM,
                    strategy_version="actual_vwap_reclaim_20260714_v1",
                    queue_card_key=queue.snapshot.items[0].card_key,
                ),
            ),
            code_version="e" * 40,
            registered_at=NOW,
            observed_at=NOW,
            minimum_clean_sessions=1,
            minimum_training_sessions=0,
            max_sessions=2,
            max_bars=500,
            per_side_fee_bps=5,
            per_side_slippage_bps=15,
            bootstrap_samples=200,
            rss_limit_gib=9.5,
            paths=IntradayActualResearchPaths(
                dataset_root=tmp_path / "dataset",
                binding_root=tmp_path / "binding",
                entitlement_contract=write_entitlement(tmp_path),
                source_queue_artifact=queue_path,
                lane_registry=lane_registry,
                experiment_ledger=ledger.path,
                artifact_root=tmp_path / "artifacts",
                review_root=tmp_path / "reviews",
            ),
        ),
        ledger,
    )
