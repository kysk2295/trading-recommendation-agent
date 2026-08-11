from __future__ import annotations

import datetime as dt
import os
import stat
from pathlib import Path

from tests.test_future_session_coordinator_service import _repository
from tests.test_future_session_coordinator_service_ready import _ready_config
from trading_agent.future_session_coordinator_service import tick_service
from trading_agent.future_session_coordinator_service_health import (
    evaluate_persisted_coordinator_health,
)
from trading_agent.future_session_coordinator_service_models import (
    canonical_service_config_sha256,
)


def test_tick_report_is_bound_to_config_sha_and_service_start(tmp_path: Path) -> None:
    config = _ready_config(tmp_path)
    authority_fixture = tmp_path / "authority-fixture"
    authority_fixture.mkdir(mode=0o700)
    repository, commit = _repository(authority_fixture)
    config = config.model_copy(update={"authority_repository": repository, "scheduler_main_sha": commit})
    started_at = dt.datetime(2026, 7, 24, 19, 59, tzinfo=dt.UTC)
    observed_at = started_at + dt.timedelta(minutes=1)

    report = tick_service(config, observed_at, service_started_at=started_at)

    assert report.config_sha256 == canonical_service_config_sha256(config)
    assert report.scheduler_main_sha == config.scheduler_main_sha
    assert report.service_started_at == started_at
    assert report.service_state == "ready"


def test_health_accepts_only_fresh_matching_ready_report(tmp_path: Path) -> None:
    config = _ready_config(tmp_path)
    started_at = dt.datetime(2026, 7, 24, 19, 59, tzinfo=dt.UTC)
    observed_at = started_at + dt.timedelta(seconds=1)
    _ = tick_service(config, observed_at, service_started_at=observed_at)

    accepted = evaluate_persisted_coordinator_health(config, started_at, observed_at)
    stale = evaluate_persisted_coordinator_health(
        config,
        observed_at + dt.timedelta(seconds=1),
        observed_at + dt.timedelta(seconds=2),
    )
    wrong_config = config.model_copy(update={"poll_interval_seconds": 31})
    mismatch = evaluate_persisted_coordinator_health(wrong_config, started_at, observed_at)

    assert accepted.accepted is True
    assert accepted.reason == "fresh_matching_ready"
    assert stale.reason == "not_fresh"
    assert mismatch.reason == "config_mismatch"


def test_service_activation_rejects_unhealthy_candidate_and_boots_it_out(
    tmp_path: Path,
) -> None:
    import run_future_session_coordinator_service as cli
    from trading_agent.future_session_coordinator_service_health import (
        FutureSessionCoordinatorHealthEvaluation,
    )
    from trading_agent.future_session_coordinator_service_launchd import (
        provision_service_plist,
    )
    from trading_agent.future_session_coordinator_service_models import (
        canonical_service_config_json,
    )

    config = _ready_config(tmp_path)
    authority_fixture = tmp_path / "authority-fixture"
    authority_fixture.mkdir(mode=0o700)
    repository, commit = _repository(authority_fixture)
    config = config.model_copy(update={"authority_repository": repository, "scheduler_main_sha": commit})
    config.state_root.mkdir(mode=0o700)
    config_path = (tmp_path / "coordinator.json").absolute()
    config_path.write_text(canonical_service_config_json(config), encoding="utf-8")
    config_path.chmod(0o600)
    _ = provision_service_plist(config, config_path)
    calls: list[tuple[str, ...]] = []

    code = cli.main(
        ("activate", "--config", str(config_path)),
        runner=lambda command, _descriptors: calls.append(command) or 0,
        health_evaluator=lambda _config, _started, _now: FutureSessionCoordinatorHealthEvaluation(
            accepted=False,
            reason="not_fresh",
            report=None,
        ),
        sleeper=lambda _seconds: None,
    )

    target = f"gui/{os.getuid()}/ai.trading-agent.future-session-coordinator"
    assert code == 2
    assert calls[-1] == ("/bin/launchctl", "bootout", target)
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_status_rejects_stale_or_config_mismatched_report(tmp_path: Path) -> None:
    import run_future_session_coordinator_service as cli
    from trading_agent.future_session_coordinator_service_models import (
        canonical_service_config_json,
    )

    config = _ready_config(tmp_path)
    observed_at = dt.datetime(2026, 7, 24, 20, tzinfo=dt.UTC)
    _ = tick_service(config, observed_at, service_started_at=observed_at)
    config_path = (tmp_path / "coordinator.json").absolute()
    config_path.write_text(canonical_service_config_json(config), encoding="utf-8")
    config_path.chmod(0o600)
    fresh = cli.main(
        ("status", "--config", str(config_path)),
        clock=lambda: observed_at + dt.timedelta(seconds=1),
    )
    stale = cli.main(
        ("status", "--config", str(config_path)),
        clock=lambda: observed_at + dt.timedelta(minutes=2),
    )
    mismatch_config = config.model_copy(update={"poll_interval_seconds": 31})
    mismatch_path = (tmp_path / "mismatch.json").absolute()
    mismatch_path.write_text(canonical_service_config_json(mismatch_config), encoding="utf-8")
    mismatch_path.chmod(0o600)
    mismatch = cli.main(
        ("status", "--config", str(mismatch_path)),
        clock=lambda: observed_at + dt.timedelta(seconds=1),
    )

    assert fresh == 0
    assert stale == mismatch == 2


def test_activation_reports_unrecoverable_cleanup_failure(tmp_path: Path, capsys) -> None:
    import run_future_session_coordinator_service as cli
    from tests.test_future_session_coordinator_service import _repository
    from trading_agent.future_session_coordinator_service_health import (
        FutureSessionCoordinatorHealthEvaluation,
    )
    from trading_agent.future_session_coordinator_service_launchd import (
        provision_service_plist,
    )
    from trading_agent.future_session_coordinator_service_models import (
        canonical_service_config_json,
    )

    config = _ready_config(tmp_path)
    authority_fixture = tmp_path / "cleanup-authority"
    authority_fixture.mkdir(mode=0o700)
    repository, commit = _repository(authority_fixture)
    config = config.model_copy(update={"authority_repository": repository, "scheduler_main_sha": commit})
    config.state_root.mkdir(mode=0o700)
    config_path = (tmp_path / "cleanup.json").absolute()
    config_path.write_text(canonical_service_config_json(config), encoding="utf-8")
    config_path.chmod(0o600)
    _ = provision_service_plist(config, config_path)
    bootout_seen = False

    def runner(command, _descriptors):
        nonlocal bootout_seen
        if command[1] == "bootout":
            bootout_seen = True
            return 1
        if command[1] == "print" and bootout_seen:
            return 0
        return 0

    code = cli.main(
        ("activate", "--config", str(config_path)),
        runner=runner,
        health_evaluator=lambda _config, _started, _now: FutureSessionCoordinatorHealthEvaluation(
            accepted=False,
            reason="runtime_failed",
            report=None,
        ),
        sleeper=lambda _seconds: None,
    )

    assert code == 2
    assert "activate_cleanup_bootout_failed" in capsys.readouterr().err
