from __future__ import annotations

import datetime as dt
import json
import os
import plistlib
import stat
import subprocess
from pathlib import Path

import pytest

import run_future_session_coordinator_service
from tests.future_session_kr_support import kr_authority_files
from tests.test_future_session_us_materializer import _authority_files
from trading_agent.future_session_coordinator_service import (
    CoordinatorAdapters,
    planning_after_date,
    prepare_market_request,
    tick_service,
)
from trading_agent.future_session_coordinator_service_launchd import (
    ServicePlistError,
    provision_service_plist,
    verify_service_plist,
)
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    FutureSessionTickAuthority,
    canonical_service_config_json,
)
from trading_agent.future_session_coordinator_service_runtime import (
    FrozenRuntimeError,
    ensure_frozen_runtime,
    load_service_config,
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


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    origin = tmp_path / "origin.git"
    subprocess.run(("git", "init", "--bare", str(origin)), check=True, capture_output=True)
    subprocess.run(("git", "init", "-b", "main", str(repository)), check=True, capture_output=True)
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "tracked.txt").write_text("authority\n", encoding="utf-8")
    (repository / "run_future_session_coordinator_service.py").write_text(
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    _git(repository, "add", "tracked.txt", "run_future_session_coordinator_service.py")
    _git(repository, "commit", "-m", "authority")
    _git(repository, "remote", "add", "origin", str(origin))
    _git(repository, "push", "-u", "origin", "main")
    return repository, _git(repository, "rev-parse", "HEAD")


def test_frozen_runtime_replays_without_duplicate_clone(tmp_path: Path) -> None:
    # Given: a clean main checkout equal to origin/main.
    repository, commit = _repository(tmp_path)
    root = tmp_path / "state" / "frozen-runtimes"

    # When: the same commit is frozen twice.
    first = ensure_frozen_runtime(repository, root)
    second = ensure_frozen_runtime(repository, root)

    # Then: the stable runtime is reused and remains an exact clean replay.
    assert first == second == root / commit
    assert stat.S_IMODE(first.stat().st_mode) == 0o700
    assert _git(first, "rev-parse", "HEAD") == commit
    assert _git(first, "status", "--porcelain", "--untracked-files=all") == ""
    assert tuple(path.name for path in root.iterdir()) == (commit,)


@pytest.mark.parametrize("mutation", ("dirty", "branch"))
def test_frozen_runtime_fails_closed_for_invalid_main(
    tmp_path: Path,
    mutation: str,
) -> None:
    # Given: authority is either dirty or no longer on main.
    repository, _commit = _repository(tmp_path)
    if mutation == "dirty":
        (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    else:
        _git(repository, "switch", "-c", "other")

    # When / Then: no runtime is created.
    with pytest.raises(FrozenRuntimeError):
        ensure_frozen_runtime(repository, tmp_path / "runtimes")
    assert not (tmp_path / "runtimes").exists()


def test_planning_cutoff_selects_today_only_before_local_cutoff() -> None:
    # Given: instants immediately around each market's planning cutoff.
    us_before = dt.datetime(2026, 8, 11, 11, 59, tzinfo=dt.UTC)
    us_after = dt.datetime(2026, 8, 11, 12, 0, tzinfo=dt.UTC)
    kr_before = dt.datetime(2026, 8, 10, 23, 29, tzinfo=dt.UTC)
    kr_after = dt.datetime(2026, 8, 10, 23, 30, tzinfo=dt.UTC)

    # When / Then: before uses yesterday as the strict after-date, after uses today.
    assert planning_after_date(FutureSessionMarket.US, us_before) == dt.date(2026, 8, 10)
    assert planning_after_date(FutureSessionMarket.US, us_after) == dt.date(2026, 8, 11)
    assert planning_after_date(FutureSessionMarket.KR, kr_before) == dt.date(2026, 8, 10)
    assert planning_after_date(FutureSessionMarket.KR, kr_after) == dt.date(2026, 8, 11)


def test_private_config_rejects_noncanonical_or_public_file(tmp_path: Path) -> None:
    # Given: a structurally valid config written with public permissions.
    config = FutureSessionCoordinatorServiceConfig(
        us_template_request_path=(tmp_path / "us.json").absolute(),
        kr_template_request_path=(tmp_path / "kr.json").absolute(),
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=(tmp_path / "repo").absolute(),
        scheduler_main_sha="a" * 40,
        poll_interval_seconds=30,
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    os.chmod(path, 0o644)

    # When / Then: the trust boundary rejects it.
    with pytest.raises(FrozenRuntimeError):
        load_service_config(path)


def test_us_request_is_target_scoped_and_reused_across_restart(tmp_path: Path) -> None:
    # Given: a canonical US template and the Friday before a Monday open session.
    repository, commit = _repository(tmp_path)
    runtime = ensure_frozen_runtime(repository, tmp_path / "runtimes")
    template = FutureSessionPlanRequest(
        market=FutureSessionMarket.US,
        after_date=dt.date(2026, 1, 1),
        compiled_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        scheduler_main_sha="a" * 40,
        authority_repository=repository,
        frozen_runtime=FrozenRuntimeAuthority(directory=runtime, commit_sha=commit),
        artifact_root=(tmp_path / "artifacts").absolute(),
        experiment_ledger=(tmp_path / "experiment.sqlite3").absolute(),
        lane_registry=(tmp_path / "lanes.sqlite3").absolute(),
        execution_database=(tmp_path / "execution.sqlite3").absolute(),
        runtime_interpreter=Path("/usr/bin/python3"),
        watch_database=(tmp_path / "watch.sqlite3").absolute(),
        delivery_database=(tmp_path / "delivery.sqlite3").absolute(),
        arm_database=(tmp_path / "arm.sqlite3").absolute(),
        signing_key=(tmp_path / "signing.env").absolute(),
        opportunity_outbox=(tmp_path / "opportunities.jsonl").absolute(),
        signal_outbox=(tmp_path / "signals.jsonl").absolute(),
        lane_review_ledger=(tmp_path / "reviews.sqlite3").absolute(),
    )
    template_path = tmp_path / "us-template.json"
    template_path.write_text(canonical_request_json(template), encoding="utf-8")
    template_path.chmod(0o600)
    config = FutureSessionCoordinatorServiceConfig(
        us_template_request_path=template_path,
        kr_template_request_path=(tmp_path / "kr-template.json").absolute(),
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=repository,
        scheduler_main_sha=commit,
        poll_interval_seconds=30,
    )
    first_time = dt.datetime(2026, 7, 3, 13, tzinfo=dt.UTC)

    # When: a restart derives the same target one minute later.
    first = prepare_market_request(
        config,
        FutureSessionMarket.US,
        FutureSessionTickAuthority(
            observed_at=first_time,
            scheduler_main_sha=commit,
            frozen_runtime=runtime,
        ),
    )
    replay = prepare_market_request(
        config,
        FutureSessionMarket.US,
        FutureSessionTickAuthority(
            observed_at=first_time + dt.timedelta(minutes=1),
            scheduler_main_sha=commit,
            frozen_runtime=runtime,
        ),
    )

    # Then: Monday's immutable request is reused with target-specific mutable stores.
    request, request_path, plan_path = first
    assert replay == first
    assert request_path == config.state_root / "requests" / "us" / "2026-07-06.json"
    assert plan_path == config.state_root / "plans" / "us" / "2026-07-06.json"
    session_root = runtime / "outputs" / "future-sessions" / "us" / "2026-07-06"
    assert request.watch_database == session_root / "paper_recommendations.sqlite3"
    assert request.opportunity_outbox == session_root / "opportunities.v1.jsonl"
    assert request.signal_outbox == session_root / "trade-signals.v1.jsonl"
    assert request.artifact_root == config.state_root / "artifacts"
    assert stat.S_IMODE(request_path.stat().st_mode) == 0o600


def test_tick_coordinates_us_then_kr_without_launching_waiting_authority(
    tmp_path: Path,
) -> None:
    # Given: both templates are canonical but their runtime ledgers bind another commit.
    repository, commit = _repository(tmp_path)
    us_root = tmp_path / "us"
    kr_root = tmp_path / "kr"
    us_root.mkdir()
    kr_root.mkdir()
    _us_request, _us_plan, us_path, _us_plan_path = _authority_files(us_root)
    _kr_request, _kr_plan, kr_path, _kr_plan_path = kr_authority_files(kr_root)
    config = FutureSessionCoordinatorServiceConfig(
        us_template_request_path=us_path,
        kr_template_request_path=kr_path,
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=repository,
        scheduler_main_sha=commit,
        poll_interval_seconds=30,
    )
    calls: list[tuple[str, ...]] = []

    # When: one tick coordinates US and then KR.
    report = tick_service(
        config,
        dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC),
        CoordinatorAdapters(
            launchctl_runner=lambda arguments: calls.append(arguments) or 0,
            label_status_reader=lambda _label: pytest.fail("launchd queried"),
        ),
    )

    # Then: each market reaches an authority terminal and no launchctl mutation occurs.
    assert report.us.result == "waiting_authority"
    assert report.kr.result == "waiting_authority"
    assert calls == []
    status_path = config.state_root / "future-session-coordinator-status.json"
    assert stat.S_IMODE(status_path.stat().st_mode) == 0o600


def test_tick_fails_closed_when_main_moves_after_config_is_frozen(
    tmp_path: Path,
) -> None:
    repository, configured_commit = _repository(tmp_path)
    us_root = tmp_path / "us"
    kr_root = tmp_path / "kr"
    us_root.mkdir()
    kr_root.mkdir()
    _us_request, _us_plan, us_path, _us_plan_path = _authority_files(us_root)
    _kr_request, _kr_plan, kr_path, _kr_plan_path = kr_authority_files(kr_root)
    config = FutureSessionCoordinatorServiceConfig(
        us_template_request_path=us_path,
        kr_template_request_path=kr_path,
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=repository,
        scheduler_main_sha=configured_commit,
        poll_interval_seconds=30,
    )
    (repository / "tracked.txt").write_text("new main\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "move main")
    _git(repository, "push", "origin", "main")

    report = tick_service(
        config,
        dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC),
    )

    assert report.scheduler_main_sha is None
    assert report.frozen_runtime is None
    assert report.us.result == "blocked"
    assert report.kr.result == "blocked"
    assert not (config.state_root / "frozen-runtimes").exists()


def test_provisioned_plist_is_keepalive_with_visible_private_logs(tmp_path: Path) -> None:
    # Given: a private service state and launch-agent destination.
    repository, commit = _repository(tmp_path)
    config = FutureSessionCoordinatorServiceConfig(
        us_template_request_path=(tmp_path / "us.json").absolute(),
        kr_template_request_path=(tmp_path / "kr.json").absolute(),
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=repository,
        scheduler_main_sha=commit,
        poll_interval_seconds=30,
    )
    config.state_root.mkdir(mode=0o700)
    config_path = (tmp_path / "config.json").absolute()

    # When: the service plist is provisioned and independently verified.
    plist_path = provision_service_plist(config, config_path)
    verified = verify_service_plist(config, config_path)

    # Then: launchd keeps it alive and routes both visible streams to stable paths.
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    assert verified == plist_path
    assert payload["KeepAlive"] is True
    assert payload["StandardOutPath"].endswith("coordinator.stdout.log")
    assert payload["StandardErrorPath"].endswith("coordinator.stderr.log")
    frozen_runtime = config.state_root / "frozen-runtimes" / commit
    assert payload["ProgramArguments"][1] == str(frozen_runtime / "run_future_session_coordinator_service.py")
    assert payload["WorkingDirectory"] == str(frozen_runtime)
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("parent_kind", ("public", "symlink"))
def test_service_plist_rejects_unprotected_launch_agent_parent(
    tmp_path: Path,
    parent_kind: str,
) -> None:
    repository, commit = _repository(tmp_path)
    launch_agents = tmp_path / "LaunchAgents"
    if parent_kind == "public":
        launch_agents.mkdir(mode=0o755)
        launch_agents.chmod(0o755)
    else:
        target = tmp_path / "launch-agent-target"
        target.mkdir(mode=0o700)
        launch_agents.symlink_to(target, target_is_directory=True)
    config = FutureSessionCoordinatorServiceConfig(
        us_template_request_path=(tmp_path / "us.json").absolute(),
        kr_template_request_path=(tmp_path / "kr.json").absolute(),
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=launch_agents.absolute(),
        authority_repository=repository,
        scheduler_main_sha=commit,
        poll_interval_seconds=30,
    )
    config.state_root.mkdir(mode=0o700)

    with pytest.raises(ServicePlistError):
        _ = provision_service_plist(config, (tmp_path / "config.json").absolute())


@pytest.mark.parametrize(
    "failed_operation",
    (None, "bootstrap", "kickstart", "print", "swap"),
)
def test_service_activation_uses_exact_launchd_target_and_rolls_back(
    tmp_path: Path,
    failed_operation: str | None,
) -> None:
    repository, commit = _repository(tmp_path)
    us_root = tmp_path / "us"
    kr_root = tmp_path / "kr"
    us_root.mkdir()
    kr_root.mkdir()
    _us_request, _us_plan, us_path, _us_plan_path = _authority_files(us_root)
    _kr_request, _kr_plan, kr_path, _kr_plan_path = kr_authority_files(kr_root)
    config = FutureSessionCoordinatorServiceConfig(
        us_template_request_path=us_path,
        kr_template_request_path=kr_path,
        state_root=(tmp_path / "state").absolute(),
        launch_agents_dir=(tmp_path / "LaunchAgents").absolute(),
        authority_repository=repository,
        scheduler_main_sha=commit,
        poll_interval_seconds=30,
    )
    config.state_root.mkdir(mode=0o700)
    config_path = (tmp_path / "config.json").absolute()
    config_path.write_text(canonical_service_config_json(config), encoding="utf-8")
    config_path.chmod(0o600)
    plist_path = provision_service_plist(config, config_path)
    calls: list[tuple[tuple[str, ...], tuple[int, ...]]] = []
    original_payload = plist_path.read_bytes()

    def runner(
        command: tuple[str, ...],
        inherited_descriptors: tuple[int, ...],
    ) -> int:
        calls.append((command, inherited_descriptors))
        if failed_operation == "swap" and command[1] == "bootstrap":
            plist_path.unlink()
            plist_path.write_bytes(b"attacker replacement")
            descriptor = inherited_descriptors[0]
            _ = os.lseek(descriptor, 0, os.SEEK_SET)
            assert os.read(descriptor, len(original_payload) + 1) == original_payload
            child = subprocess.run(
                ("/usr/bin/plutil", "-lint", command[3]),
                check=False,
                capture_output=True,
                pass_fds=inherited_descriptors,
            )
            assert child.returncode == 0
        return int(failed_operation is not None and command[1] == failed_operation)

    result = run_future_session_coordinator_service.main(
        ("activate", "--config", str(config_path)),
        runner=runner,
    )

    domain = f"gui/{os.getuid()}"
    target = f"{domain}/ai.trading-agent.future-session-coordinator"
    bootstrap, inherited = calls[0]
    assert bootstrap[:3] == ("/bin/launchctl", "bootstrap", domain)
    assert len(inherited) == 1
    assert bootstrap[3] == f"/dev/fd/{inherited[0]}"
    commands = [command for command, _descriptors in calls]
    assert all(not descriptors for _command, descriptors in calls[1:])
    if failed_operation == "bootstrap":
        assert len(commands) == 1
        assert result == 2
    elif failed_operation == "swap":
        assert commands[1:] == [("/bin/launchctl", "bootout", target)]
        assert result == 2
    elif failed_operation is None:
        assert commands[1:] == [
            ("/bin/launchctl", "kickstart", target),
            ("/bin/launchctl", "print", target),
        ]
        assert result == 0
    else:
        expected = [("/bin/launchctl", "kickstart", target)]
        if failed_operation == "print":
            expected.append(("/bin/launchctl", "print", target))
        expected.append(("/bin/launchctl", "bootout", target))
        assert commands[1:] == expected
        assert result == 2
