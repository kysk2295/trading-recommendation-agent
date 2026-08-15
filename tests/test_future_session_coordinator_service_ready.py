from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_forward_runtime_readiness_cli import _runtime, _stores
from tests.test_future_session_coordinator import _LaunchdFixture
from trading_agent.future_session_coordinator_service import (
    CoordinatorAdapters,
    tick_service,
)
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.future_session_coordinator_service_runtime import ensure_frozen_runtime
from trading_agent.future_session_execution_incident import (
    FutureSessionExecutionIncidentReceipt,
    InvalidFutureSessionExecutionIncidentError,
    canonical_execution_incident_json,
)
from trading_agent.future_session_execution_incident_queue import (
    MAX_PENDING_EXECUTION_INCIDENTS,
    FutureSessionExecutionIncidentQueuePointer,
    canonical_execution_incident_queue_json,
    execution_incident_queue_path,
    project_pending_execution_incidents,
)
from trading_agent.future_session_materialization_models import FutureSessionPreparationManifest
from trading_agent.future_session_plan_models import (
    FrozenRuntimeAuthority,
    FutureSessionMarket,
    FutureSessionPlanRequest,
    canonical_request_json,
)
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore


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
    publisher = repository / "run_future_session_execution_incident_publisher.py"
    publisher.write_bytes(
        (Path(__file__).parents[1] / "run_future_session_execution_incident_publisher.py").read_bytes()
    )
    _git(repository, "add", ".gitignore", publisher.name)
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
        us_template_sha256=hashlib.sha256(canonical_request_json(us).encode()).hexdigest(),
        kr_template_sha256=hashlib.sha256(canonical_request_json(kr).encode()).hexdigest(),
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=repository,
        scheduler_main_sha=head,
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


def test_ready_us_tick_activates_frozen_authority_after_main_advances(
    tmp_path: Path,
) -> None:
    # Given: the configured runtime is frozen before mutable local and origin main advance.
    config = _ready_config(tmp_path)
    frozen = ensure_frozen_runtime(
        config.authority_repository,
        config.state_root / "frozen-runtimes",
        config.scheduler_main_sha,
    )
    _git(config.authority_repository, "commit", "--allow-empty", "--quiet", "-m", "advance main")
    _git(config.authority_repository, "push", "origin", "main")
    launchd = _LaunchdFixture()
    adapters = CoordinatorAdapters(
        launchctl_runner=launchd.run,
        label_status_reader=launchd.is_loaded,
    )

    # When: the old configured Coordinator performs its first ready tick.
    report = tick_service(
        config,
        dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC),
        adapters,
    )

    # Then: activation remains bound to the clean exact frozen SHA.
    assert report.frozen_runtime == frozen
    assert report.us.receipt is not None
    assert report.us.receipt.preparation == "prepared"
    assert report.us.receipt.activation == "activated"


def test_tick_projects_materialized_execution_incident_once(tmp_path: Path) -> None:
    config = _ready_config(tmp_path)
    launchd = _LaunchdFixture()
    adapters = CoordinatorAdapters(
        launchctl_runner=launchd.run,
        label_status_reader=launchd.is_loaded,
    )
    observed_at = dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC)
    prepared = tick_service(config, observed_at, adapters)
    assert prepared.us.request_path is not None
    assert prepared.us.receipt is not None
    assert prepared.us.receipt.manifest_path is not None
    request = FutureSessionPlanRequest.model_validate_json(prepared.us.request_path.read_bytes())
    manifest_path = prepared.us.receipt.manifest_path
    manifest = FutureSessionPreparationManifest.model_validate_json(manifest_path.read_bytes())
    incident = FutureSessionExecutionIncidentReceipt(
        completed_at_epoch=int(dt.datetime(2026, 7, 27, 16, tzinfo=dt.UTC).timestamp()),
        market=FutureSessionMarket.US,
        target_session=dt.date(2026, 7, 27),
        role="us_orb_watcher",
        reason="runtime_authority_invalid",
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        request_sha256=manifest.request_sha256,
        plan_sha256=manifest.plan_sha256,
        scheduler_main_sha=manifest.scheduler_main_sha,
        runtime_commit_sha=manifest.runtime_commit_sha,
    )
    incident_path = manifest_path.parent / "execution-incidents" / "us_orb_watcher.json"
    incident_path.write_text(canonical_execution_incident_json(incident), encoding="utf-8")
    incident_path.chmod(0o600)
    queue_path = execution_incident_queue_path(
        config.state_root,
        incident.market,
        incident.target_session,
        incident.role,
    )
    pointer = FutureSessionExecutionIncidentQueuePointer(
        market=incident.market,
        target_session=incident.target_session,
        role=incident.role,
        incident_sha256=hashlib.sha256(incident_path.read_bytes()).hexdigest(),
    )
    queue_path.write_text(canonical_execution_incident_queue_json(pointer), encoding="utf-8")
    queue_path.chmod(0o600)

    foreign_config = config.model_copy(update={"scheduler_main_sha": "f" * 40})
    with pytest.raises(InvalidFutureSessionExecutionIncidentError):
        project_pending_execution_incidents(foreign_config)
    assert request.delivery_database is not None
    assert not request.delivery_database.exists()

    first = tick_service(config, observed_at + dt.timedelta(minutes=1), adapters)
    assert not queue_path.exists()
    incident_path.write_text("tampered historical incident\n", encoding="utf-8")
    replay = tick_service(config, observed_at + dt.timedelta(minutes=2), adapters)

    assert first.us.result == "blocked"
    assert first.us.reason == "execution_incident"
    assert replay.us.result != "blocked"
    events = HermesDeliveryStore(request.delivery_database).events()
    assert len(events) == 1
    assert events[0].kind is HermesDeliveryKind.INCIDENT

    next_session = tick_service(
        config,
        dt.datetime(2026, 7, 27, 17, tzinfo=dt.UTC),
        adapters,
    )
    assert next_session.us.result != "blocked"
    assert next_session.us.receipt is not None
    assert next_session.us.receipt.target_session != incident.target_session


def test_pending_execution_incident_queue_is_bounded(tmp_path: Path) -> None:
    config = _ready_config(tmp_path)
    queue_root = config.state_root / "pending-execution-incidents"
    queue_root.mkdir(parents=True, mode=0o700)
    for index in range(MAX_PENDING_EXECUTION_INCIDENTS + 1):
        path = queue_root / f"fixture-{index:03d}.json"
        path.write_text("{}\n", encoding="utf-8")
        path.chmod(0o600)

    with pytest.raises(InvalidFutureSessionExecutionIncidentError):
        project_pending_execution_incidents(config)
