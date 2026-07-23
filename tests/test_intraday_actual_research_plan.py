from __future__ import annotations

import datetime as dt
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.challenger_replay_fixtures import write_closed_source_session
from tests.intraday_research_input_binding_fixtures import NOW
from tests.test_intraday_actual_research import _request
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.intraday_actual_research_plan import (
    IntradayActualResearchPlanError,
    run_planned_intraday_actual_research,
)
from trading_agent.intraday_actual_research_plan_models import (
    IntradayActualResearchPlanPaths,
    IntradayActualResearchRunSpec,
)

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_planned_intraday_actual_research.py"


def test_planned_actual_research_freezes_queue_and_replays_exact_plan(
    tmp_path: Path,
) -> None:
    request, ledger = _request(tmp_path)
    spec = _spec(request, run_key="actual-2026-07-14")

    first = run_planned_intraday_actual_research(
        spec,
        plan_root=tmp_path / "plans",
        queue_root=tmp_path / "planned-queue",
        observed_at=NOW,
    )
    replay = run_planned_intraday_actual_research(
        spec,
        plan_root=tmp_path / "plans",
        queue_root=tmp_path / "planned-queue",
        observed_at=NOW + dt.timedelta(minutes=1),
    )

    assert first.plan_created is True
    assert first.queue_created is True
    assert replay.plan_created is False
    assert replay.queue_created is False
    assert replay.plan.plan_id == first.plan.plan_id
    assert replay.plan.content.source_queue_snapshot_id == first.plan.content.source_queue_snapshot_id
    assert replay.actual.loop.experiment_artifacts_created == 0
    assert replay.actual.loop.review_artifacts_created == 0
    assert len(ExperimentLedgerReader(ledger.path).strategy_versions()) == 1
    assert len(ExperimentLedgerReader(ledger.path).trials()) == 1
    assert stat.S_IMODE(first.plan_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(first.plan.content.source_queue_artifact.stat().st_mode) == 0o600


def test_planned_actual_research_refreshes_same_versions_on_next_run_key(
    tmp_path: Path,
) -> None:
    request, ledger = _request(tmp_path)
    first_spec = _spec(request, run_key="actual-2026-07-14")
    first = run_planned_intraday_actual_research(
        first_spec,
        plan_root=tmp_path / "plans",
        queue_root=tmp_path / "planned-queue",
        observed_at=NOW,
    )
    second_source = tmp_path / "2026-07-15"
    write_closed_source_session(second_source, session_date=dt.date(2026, 7, 15))
    second_spec = first_spec.model_copy(
        update={
            "run_key": "actual-2026-07-15",
            "session_dirs": (*first_spec.session_dirs, second_source),
            "required_session_dates": (dt.date(2026, 7, 15),),
            "registered_at": NOW + dt.timedelta(days=1),
            "max_bars": 1_000,
        }
    )

    second = run_planned_intraday_actual_research(
        second_spec,
        plan_root=tmp_path / "plans",
        queue_root=tmp_path / "planned-queue",
        observed_at=NOW + dt.timedelta(days=1),
    )

    reader = ExperimentLedgerReader(ledger.path)
    assert second.plan.plan_id != first.plan.plan_id
    assert second.plan.content.source_queue_snapshot_id != first.plan.content.source_queue_snapshot_id
    assert first.actual.catalog.dataset.session_count == 1
    assert second.actual.catalog.dataset.session_count == 2
    assert len(reader.strategy_versions()) == 1
    assert len(reader.trials()) == 2


def test_planned_actual_research_blocks_run_key_spec_drift_before_trial(
    tmp_path: Path,
) -> None:
    request, ledger = _request(tmp_path)
    spec = _spec(request, run_key="actual-2026-07-14")
    first = run_planned_intraday_actual_research(
        spec,
        plan_root=tmp_path / "plans",
        queue_root=tmp_path / "planned-queue",
        observed_at=NOW,
    )
    changed = spec.model_copy(update={"max_bars": spec.max_bars + 1})

    with pytest.raises(IntradayActualResearchPlanError):
        _ = run_planned_intraday_actual_research(
            changed,
            plan_root=tmp_path / "plans",
            queue_root=tmp_path / "planned-queue",
            observed_at=NOW + dt.timedelta(minutes=1),
        )

    reader = ExperimentLedgerReader(ledger.path)
    assert first.actual.loop.trials_total == 1
    assert len(reader.strategy_versions()) == 1
    assert len(reader.trials()) == 1


def test_planned_actual_research_cli_exposes_plan_boundary_and_rejects_bad_binding() -> None:
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
    assert "--run-key" in help_result.stdout
    assert "--plan-dir" in help_result.stdout
    assert "--queue-dir" in help_result.stdout
    assert "--required-session-date" in help_result.stdout
    assert "--strategy-binding" in help_result.stdout
    assert bad_result.returncode == 2


def _spec(request, *, run_key: str) -> IntradayActualResearchRunSpec:
    return IntradayActualResearchRunSpec(
        run_key=run_key,
        session_dirs=request.session_dirs,
        required_session_dates=request.required_session_dates,
        strategy_bindings=request.strategy_bindings,
        code_version=request.code_version,
        registered_at=request.registered_at,
        minimum_clean_sessions=request.minimum_clean_sessions,
        minimum_training_sessions=request.minimum_training_sessions,
        max_sessions=request.max_sessions,
        max_bars=request.max_bars,
        per_side_fee_bps=request.per_side_fee_bps,
        per_side_slippage_bps=request.per_side_slippage_bps,
        bootstrap_samples=request.bootstrap_samples,
        rss_limit_gib=request.rss_limit_gib,
        paths=IntradayActualResearchPlanPaths(
            dataset_root=request.paths.dataset_root,
            binding_root=request.paths.binding_root,
            entitlement_contract=request.paths.entitlement_contract,
            lane_registry=request.paths.lane_registry,
            experiment_ledger=request.paths.experiment_ledger,
            artifact_root=request.paths.artifact_root,
            review_root=request.paths.review_root,
        ),
    )
