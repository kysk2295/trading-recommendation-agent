from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

from tests.test_forward_runtime_readiness_cli import _runtime, _stores
from tests.test_future_session_coordinator import _LaunchdFixture
from trading_agent.future_session_coordinator_service import (
    CoordinatorAdapters,
    tick_service,
)
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.future_session_plan_models import (
    FrozenRuntimeAuthority,
    FutureSessionMarket,
    FutureSessionPlanRequest,
    canonical_request_json,
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _ready_config(tmp_path: Path) -> FutureSessionCoordinatorServiceConfig:
    repository, required, _initial_head = _runtime(tmp_path)
    (repository / ".gitignore").write_text("/outputs/\n", encoding="utf-8")
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "--quiet", "-m", "ignore durable outputs")
    head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "branch", "-M", "main")
    origin = tmp_path / "origin.git"
    subprocess.run(("git", "init", "--bare", str(origin)), check=True, capture_output=True)
    _git(repository, "remote", "add", "origin", str(origin))
    _git(repository, "push", "-u", "origin", "main")
    lane, experiment, execution = _stores(tmp_path, code_version=head)
    authority = FrozenRuntimeAuthority(directory=repository, commit_sha=head)
    common = {
        "after_date": dt.date(2026, 7, 24),
        "compiled_at": dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC),
        "scheduler_main_sha": head,
        "authority_repository": repository,
        "frozen_runtime": authority,
        "artifact_root": (tmp_path / "template-artifacts").absolute(),
        "experiment_ledger": experiment.absolute(),
    }
    us = FutureSessionPlanRequest(
        market=FutureSessionMarket.US,
        lane_registry=lane.absolute(),
        execution_database=execution.absolute(),
        required_runtime_commits=(required,),
        runtime_interpreter=Path(sys.executable).absolute(),
        watch_database=(repository / "outputs" / "template" / "paper_recommendations.sqlite3").absolute(),
        delivery_database=(tmp_path / "delivery.sqlite3").absolute(),
        arm_database=(tmp_path / "arm.sqlite3").absolute(),
        signing_key=(tmp_path / "signing.env").absolute(),
        opportunity_outbox=(tmp_path / "opportunities.sqlite3").absolute(),
        signal_outbox=(tmp_path / "signals.sqlite3").absolute(),
        lane_review_ledger=(tmp_path / "lane-review.sqlite3").absolute(),
        **common,
    )
    kr = FutureSessionPlanRequest(
        market=FutureSessionMarket.KR,
        kr_calendar_store=(tmp_path / "missing-calendar.sqlite3").absolute(),
        kr_rollover_bundle=(tmp_path / "missing-rollover.json").absolute(),
        **common,
    )
    us_path = tmp_path / "us-template.json"
    kr_path = tmp_path / "kr-template.json"
    for path, request in ((us_path, us), (kr_path, kr)):
        path.write_text(canonical_request_json(request), encoding="utf-8")
        path.chmod(0o600)
    return FutureSessionCoordinatorServiceConfig(
        us_template_request_path=us_path,
        kr_template_request_path=kr_path,
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=repository,
        poll_interval_seconds=30,
    )


def test_ready_us_tick_materializes_and_replays_without_duplicate_launchctl(
    tmp_path: Path,
) -> None:
    # Given: current main is a complete US planning authority with ignored durable outputs.
    config = _ready_config(tmp_path)
    launchd = _LaunchdFixture()
    adapters = CoordinatorAdapters(
        launchctl_runner=launchd.run,
        label_status_reader=launchd.is_loaded,
    )

    # When: the service ticks and then restarts against the same target session.
    first = tick_service(config, dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC), adapters)
    first_calls = tuple(launchd.calls)
    replay = tick_service(config, dt.datetime(2026, 7, 24, 20, 1, tzinfo=dt.UTC), adapters)

    # Then: preparation and activation happen once, with runtime outputs and private artifacts.
    assert first.us.receipt is not None
    assert first.us.receipt.preparation == "prepared"
    assert first.us.receipt.activation == "activated"
    assert replay.us.receipt is not None
    assert replay.us.receipt.preparation == "already_prepared"
    assert replay.us.receipt.activation == "already_activated"
    assert tuple(launchd.calls) == first_calls
    assert replay.us.request_path is not None
    request = FutureSessionPlanRequest.model_validate_json(replay.us.request_path.read_bytes())
    assert request.watch_database is not None
    assert request.watch_database.is_relative_to(request.frozen_runtime.directory / "outputs")
    assert request.opportunity_outbox is not None
    assert request.opportunity_outbox.is_relative_to(request.frozen_runtime.directory / "outputs")
    assert request.signal_outbox is not None
    assert request.signal_outbox.is_relative_to(request.frozen_runtime.directory / "outputs")
    assert request.watch_database.parent == request.opportunity_outbox.parent
    assert request.watch_database.parent == request.signal_outbox.parent
    assert request.watch_database.name == "paper_recommendations.sqlite3"
    assert request.opportunity_outbox.name == "opportunities.v1.jsonl"
    assert request.signal_outbox.name == "trade-signals.v1.jsonl"
    assert replay.us.receipt.manifest_path is not None
    assert replay.us.receipt.manifest_path.is_relative_to(config.state_root / "artifacts")
