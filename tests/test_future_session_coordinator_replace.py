from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import run_future_session_coordinator_service as cli
from tests.test_future_session_coordinator_service import _git, _repository
from tests.test_future_session_coordinator_service_ready import _ready_config
from trading_agent.future_session_coordinator_service import (
    CoordinatorAdapters,
    tick_service,
)
from trading_agent.future_session_coordinator_service_health import (
    FutureSessionCoordinatorHealthEvaluation,
    evaluate_persisted_coordinator_health,
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
    canonical_service_config_sha256,
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


def test_replace_rejects_loaded_child_job_before_stopping_current(tmp_path: Path, capsys) -> None:
    replacement = _replacement(tmp_path)
    child = replacement.current.launch_agents_dir / "ai.trading-agent.us-orb-watcher-20260727.plist"
    child.write_text("fixture\n", encoding="utf-8")
    child.chmod(0o600)
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], _descriptors: tuple[int, ...]) -> int:
        calls.append(command)
        return 0

    code = cli.main(
        (
            "replace",
            "--current-config",
            str(replacement.current_path),
            "--candidate-config",
            str(replacement.candidate_path),
        ),
        runner=runner,
        health_evaluator=_healthy,
    )

    assert code == 2
    assert calls == [
        (
            "/bin/launchctl",
            "print",
            f"gui/{os.getuid()}/ai.trading-agent.us-orb-watcher-20260727",
        )
    ]
    assert "replace_active_child_job" in capsys.readouterr().err


def test_replace_finds_loaded_child_from_manifest_without_plist(tmp_path: Path, capsys) -> None:
    replacement = _replacement(tmp_path)
    label = "ai.trading-agent.future-session.kr.2026-07-27.supervisor"
    manifest = replacement.current.state_root / "artifacts" / "kr" / "2026-07-27" / "preparation-manifest.json"
    manifest.parent.mkdir(parents=True, mode=0o700)
    manifest.write_text(json.dumps({"entry": {"label": label}}), encoding="utf-8")
    manifest.chmod(0o600)
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

    assert code == 2
    assert calls == [("/bin/launchctl", "print", f"gui/{os.getuid()}/{label}")]
    assert "replace_active_child_job" in capsys.readouterr().err


def test_replace_restores_current_when_child_appears_at_stop_boundary(tmp_path: Path, capsys) -> None:
    replacement = _replacement(tmp_path)
    label = "ai.trading-agent.us-orb-watcher-20260727"
    child = replacement.current.launch_agents_dir / f"{label}.plist"
    child.write_text("fixture\n", encoding="utf-8")
    child.chmod(0o600)
    calls: list[tuple[str, ...]] = []
    child_checks = 0
    restored_payload = b""

    def runner(command: tuple[str, ...], descriptors: tuple[int, ...]) -> int:
        nonlocal child_checks, restored_payload
        calls.append(command)
        if command[1] == "print" and command[-1].endswith(label):
            child_checks += 1
            return 113 if child_checks == 1 else 0
        if command[1] == "bootstrap":
            restored_payload = os.pread(descriptors[0], 1024 * 1024, 0)
        return 0

    code = cli.main(
        (
            "replace",
            "--current-config",
            str(replacement.current_path),
            "--candidate-config",
            str(replacement.candidate_path),
        ),
        runner=runner,
        health_evaluator=_healthy,
    )

    assert code == 2
    assert child_checks == 2
    assert (
        restored_payload
        == (replacement.current.launch_agents_dir / "ai.trading-agent.future-session-coordinator.plist").read_bytes()
    )
    assert "replace_child_inventory_changed" in capsys.readouterr().err
    assert [command[1] for command in calls].count("bootstrap") == 1


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


def test_replace_cleans_candidate_children_before_restoring_current(tmp_path: Path) -> None:
    replacement = _replacement(tmp_path)
    label = "ai.trading-agent.future-session.kr.2026-07-27.supervisor"
    loaded: set[str] = set()
    calls: list[tuple[str, ...]] = []
    candidate_plist: Path | None = None

    def runner(command: tuple[str, ...], _descriptors: tuple[int, ...]) -> int:
        calls.append(command)
        target = command[-1]
        if target.endswith(label):
            if command[1] == "print":
                return 0 if label in loaded else 113
            if command[1] == "bootout":
                loaded.discard(label)
                return 0
        return 0

    def health(config, _started, _now):
        nonlocal candidate_plist
        if config == replacement.candidate:
            candidate_plist = _candidate_child(
                replacement.candidate,
                label,
                include_manifest=False,
            )
            loaded.add(label)
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
        runner=runner,
        health_evaluator=health,
        sleeper=lambda _seconds: None,
    )

    assert code == 2
    assert candidate_plist is not None
    assert not candidate_plist.exists()
    assert label not in loaded
    child_bootout = calls.index(("/bin/launchctl", "bootout", f"gui/{os.getuid()}/{label}"))
    restore_bootstrap = max(index for index, command in enumerate(calls) if command[1] == "bootstrap")
    assert child_bootout < restore_bootstrap
    config_sha256 = canonical_service_config_sha256(replacement.candidate)
    cleanup = replacement.candidate.state_root / "replacement-child-cleanup" / f"{config_sha256}.json"
    assert cleanup.stat().st_mode & 0o777 == 0o600
    assert json.loads(cleanup.read_text(encoding="utf-8")) == {
        "config_sha256": config_sha256,
        "labels": [label],
        "result": "absent",
        "scheduler_main_sha": replacement.candidate.scheduler_main_sha,
        "schema_version": 1,
    }


def test_replace_refuses_current_restore_when_candidate_child_cleanup_is_ambiguous(
    tmp_path: Path,
    capsys,
) -> None:
    replacement = _replacement(tmp_path)
    label = "ai.trading-agent.future-session.kr.2026-07-27.supervisor"
    loaded: set[str] = set()
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], _descriptors: tuple[int, ...]) -> int:
        calls.append(command)
        target = command[-1]
        if target.endswith(label):
            if command[1] == "print":
                return 0 if label in loaded else 113
            if command[1] == "bootout":
                loaded.discard(label)
                return 0
        return 0

    def health(config, _started, _now):
        if config == replacement.candidate:
            child = _candidate_child(replacement.candidate, label)
            child.chmod(0o644)
            loaded.add(label)
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
        runner=runner,
        health_evaluator=health,
        sleeper=lambda _seconds: None,
    )

    assert code == 2
    assert [command[1] for command in calls].count("bootstrap") == 1
    assert "replace_candidate_child_cleanup_failed" in capsys.readouterr().err


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


def test_replace_failed_current_health_stops_restored_service(
    tmp_path: Path,
    capsys,
) -> None:
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
        health_evaluator=lambda _config, _started, _now: FutureSessionCoordinatorHealthEvaluation(
            accepted=False,
            reason="runtime_failed",
            report=None,
        ),
        sleeper=lambda _seconds: None,
    )

    assert code == 2
    assert [command[1] for command in calls][-1] == "bootout"
    assert "replace_current_restore_health_runtime_failed" in capsys.readouterr().err


def test_replace_candidate_cleanup_failure_blocks_current_restore(
    tmp_path: Path,
    capsys,
) -> None:
    replacement = _replacement(tmp_path)
    calls: list[tuple[str, ...]] = []
    bootouts = 0

    def runner(command, _descriptors):
        nonlocal bootouts
        calls.append(command)
        if command[1] == "bootout":
            bootouts += 1
            return int(bootouts == 2)
        return 0

    code = cli.main(
        (
            "replace",
            "--current-config",
            str(replacement.current_path),
            "--candidate-config",
            str(replacement.candidate_path),
        ),
        runner=runner,
        health_evaluator=lambda _config, _started, _now: FutureSessionCoordinatorHealthEvaluation(
            accepted=False,
            reason="runtime_failed",
            report=None,
        ),
        sleeper=lambda _seconds: None,
    )

    assert code == 2
    assert [command[1] for command in calls] == [
        "bootout",
        "bootstrap",
        "kickstart",
        "print",
        "bootout",
        "print",
    ]
    assert "replace_candidate_cleanup_bootout_failed" in capsys.readouterr().err


def test_replace_partial_current_restore_is_stopped(tmp_path: Path, capsys) -> None:
    replacement = _replacement(tmp_path)
    calls: list[tuple[str, ...]] = []
    kickstarts = 0

    def runner(command, _descriptors):
        nonlocal kickstarts
        calls.append(command)
        if command[1] == "kickstart":
            kickstarts += 1
            return int(kickstarts == 2)
        return 0

    code = cli.main(
        (
            "replace",
            "--current-config",
            str(replacement.current_path),
            "--candidate-config",
            str(replacement.candidate_path),
        ),
        runner=runner,
        health_evaluator=lambda _config, _started, _now: FutureSessionCoordinatorHealthEvaluation(
            accepted=False,
            reason="runtime_failed",
            report=None,
        ),
        sleeper=lambda _seconds: None,
    )

    assert code == 2
    assert [command[1] for command in calls][-2:] == ["kickstart", "bootout"]
    assert "replace_current_restore_start_failed" in capsys.readouterr().err


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


def _candidate_child(
    config: FutureSessionCoordinatorServiceConfig,
    label: str,
    *,
    include_manifest: bool = True,
) -> Path:
    manifest = config.state_root / "artifacts" / "kr" / "2026-07-27" / "preparation-manifest.json"
    if include_manifest:
        manifest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not manifest.exists():
            manifest.write_text(json.dumps({"entry": {"label": label}}), encoding="utf-8")
            manifest.chmod(0o600)
    config.launch_agents_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    child = config.launch_agents_dir / f"{label}.plist"
    if not child.exists():
        child.write_text("fixture\n", encoding="utf-8")
        child.chmod(0o600)
    return child


def _healthy(config, started, now) -> FutureSessionCoordinatorHealthEvaluation:
    _ = tick_service(
        config,
        now,
        CoordinatorAdapters(
            launchctl_runner=lambda _command: 0,
            label_status_reader=lambda _label: False,
        ),
        service_started_at=now,
    )
    return evaluate_persisted_coordinator_health(config, started, now)
