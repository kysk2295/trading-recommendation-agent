from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import run_future_session_coordinator_service as cli
from tests.test_future_session_coordinator_service import _git, _repository
from tests.test_future_session_coordinator_service_ready import _ready_config
from trading_agent.future_session_coordinator_service_health import (
    FutureSessionCoordinatorHealthEvaluation,
)
from trading_agent.future_session_coordinator_service_launchd import (
    provision_service_plist,
)
from trading_agent.future_session_coordinator_service_lifecycle import (
    verify_coordinator_authority,
)
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    canonical_service_config_json,
)


@dataclass(frozen=True, slots=True)
class _Replacement:
    current: FutureSessionCoordinatorServiceConfig
    current_path: Path
    candidate: FutureSessionCoordinatorServiceConfig
    candidate_path: Path


def test_replace_upgrades_exact_sha_after_fresh_candidate_health(tmp_path: Path) -> None:
    replacement = _replacement(tmp_path)
    calls: list[tuple[str, ...]] = []

    code = cli.main(
        (
            "replace",
            "--current-config",
            str(replacement.current_path),
            "--candidate-config",
            str(replacement.candidate_path),
        ),
        runner=lambda command, _descriptors: calls.append(command) or 0,
        health_evaluator=_healthy,
    )

    target = f"gui/{os.getuid()}/ai.trading-agent.future-session-coordinator"
    assert code == 0
    assert replacement.current.scheduler_main_sha != replacement.candidate.scheduler_main_sha
    assert [command[1] for command in calls] == ["bootout", "bootstrap", "kickstart", "print"]
    assert calls[0] == ("/bin/launchctl", "bootout", target)


def test_replace_unhealthy_candidate_restores_fresh_current(tmp_path: Path) -> None:
    replacement = _replacement(tmp_path)
    calls: list[tuple[str, ...]] = []

    def health(config, _started, _now):
        if config == replacement.candidate:
            return FutureSessionCoordinatorHealthEvaluation(
                accepted=False,
                reason="runtime_failed",
                report=None,
            )
        return _healthy(config, _started, _now)

    code = cli.main(
        (
            "replace",
            "--current-config",
            str(replacement.current_path),
            "--candidate-config",
            str(replacement.candidate_path),
        ),
        runner=lambda command, _descriptors: calls.append(command) or 0,
        health_evaluator=health,
        sleeper=lambda _seconds: None,
    )

    assert code == 2
    assert [command[1] for command in calls] == [
        "bootout",
        "bootstrap",
        "kickstart",
        "print",
        "bootout",
        "bootstrap",
        "kickstart",
        "print",
    ]


def test_restart_reloads_same_pinned_config_and_requires_fresh_health(tmp_path: Path) -> None:
    replacement = _replacement(tmp_path)
    calls: list[tuple[str, ...]] = []

    code = cli.main(
        ("restart", "--config", str(replacement.current_path)),
        runner=lambda command, _descriptors: calls.append(command) or 0,
        health_evaluator=_healthy,
    )

    assert code == 0
    assert [command[1] for command in calls] == ["bootout", "bootstrap", "kickstart", "print"]


def _replacement(tmp_path: Path) -> _Replacement:
    fixture = tmp_path / "fixture"
    fixture.mkdir(mode=0o700)
    base = _ready_config(fixture)
    authority = tmp_path / "authority"
    authority.mkdir(mode=0o700)
    repository, current_sha = _repository(authority)
    current = base.model_copy(
        update={
            "state_root": (tmp_path / "current-state").absolute(),
            "launch_agents_dir": (tmp_path / "current-launch").absolute(),
            "authority_repository": repository,
            "scheduler_main_sha": current_sha,
        }
    )
    current_path = _write_and_provision(tmp_path / "current.json", current)
    (repository / "tracked.txt").write_text("candidate\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "candidate")
    _git(repository, "push", "origin", "main")
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    candidate = base.model_copy(
        update={
            "state_root": (tmp_path / "candidate-state").absolute(),
            "launch_agents_dir": (tmp_path / "candidate-launch").absolute(),
            "authority_repository": repository,
            "scheduler_main_sha": candidate_sha,
        }
    )
    candidate_path = _write_and_provision(tmp_path / "candidate.json", candidate)
    return _Replacement(current, current_path, candidate, candidate_path)


def _write_and_provision(
    path: Path,
    config: FutureSessionCoordinatorServiceConfig,
) -> Path:
    path.write_text(canonical_service_config_json(config), encoding="utf-8")
    path.chmod(0o600)
    verify_coordinator_authority(config)
    _ = provision_service_plist(config, path)
    return path


def _healthy(_config, _started, _now) -> FutureSessionCoordinatorHealthEvaluation:
    return FutureSessionCoordinatorHealthEvaluation(
        accepted=True,
        reason="fresh_matching_ready",
        report=None,
    )
