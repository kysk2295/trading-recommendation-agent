from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import run_local_browser_gateway as cli
from run_local_browser_gateway import main
from tests.test_local_browser_gateway_cli import _fixture_config, _provision_args
from trading_agent.repository_current_main import CurrentMainAuthorityError


@pytest.mark.parametrize("failure", (OSError(), subprocess.SubprocessError(), RuntimeError()))
def test_activate_boots_out_exact_plist_when_kickstart_runner_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
) -> None:
    # Given: a verified current-main contract and a kickstart runner exception.
    config, config_path, plist_path = _fixture_config(tmp_path)
    assert main(_provision_args(config, config_path, plist_path)) == 0
    _ = capsys.readouterr()
    monkeypatch.setattr(cli, "current_main_commit", lambda _repository: "a" * 40)
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        if command[1] == "kickstart":
            raise failure
        return 0

    # When: activation crosses the post-bootstrap exception path.
    result = main(("activate", "--config", str(config_path), "--plist", str(plist_path)), runner=runner)
    # Then: exact cleanup is attempted once and the error remains redacted.
    domain = f"gui/{os.getuid()}"
    assert result == 2
    assert calls == [
        ("/bin/launchctl", "bootstrap", domain, str(plist_path)),
        ("/bin/launchctl", "kickstart", f"{domain}/{config.label}"),
        ("/bin/launchctl", "bootout", domain, str(plist_path)),
    ]
    captured = capsys.readouterr()
    assert captured.out == "" and "Traceback" not in captured.err


def test_activate_preserves_exit_two_when_bootout_runner_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: successful bootstrap, nonzero kickstart, and a bootout exception.
    config, config_path, plist_path = _fixture_config(tmp_path)
    assert main(_provision_args(config, config_path, plist_path)) == 0
    _ = capsys.readouterr()
    monkeypatch.setattr(cli, "current_main_commit", lambda _repository: "a" * 40)
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        if command[1] == "bootout":
            raise OSError
        return int(command[1] == "kickstart")

    # When: rollback itself raises.
    result = main(("activate", "--config", str(config_path), "--plist", str(plist_path)), runner=runner)
    # Then: cleanup is single-attempt, exact, redacted, and remains exit two.
    domain = f"gui/{os.getuid()}"
    assert result == 2
    assert calls == [
        ("/bin/launchctl", "bootstrap", domain, str(plist_path)),
        ("/bin/launchctl", "kickstart", f"{domain}/{config.label}"),
        ("/bin/launchctl", "bootout", domain, str(plist_path)),
    ]
    captured = capsys.readouterr()
    assert captured.out == "" and "Traceback" not in captured.err


def test_activate_does_not_bootout_when_bootstrap_runner_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a verified contract whose bootstrap runner raises.
    config, config_path, plist_path = _fixture_config(tmp_path)
    assert main(_provision_args(config, config_path, plist_path)) == 0
    monkeypatch.setattr(cli, "current_main_commit", lambda _repository: "a" * 40)
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        calls.append(command)
        raise OSError

    # When: bootstrap fails before the service is added.
    result = main(("activate", "--config", str(config_path), "--plist", str(plist_path)), runner=runner)
    # Then: no cleanup is attempted for an unbootstrapped service.
    assert result == 2
    assert calls == [("/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path))]


def test_activate_stops_before_launchctl_when_authority_guard_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a verified contract and an authority guard that records then rejects its repository.
    config, config_path, plist_path = _fixture_config(tmp_path)
    assert main(_provision_args(config, config_path, plist_path)) == 0
    authority_repositories: list[Path] = []
    launchctl_calls: list[tuple[str, ...]] = []

    def reject_authority(repository: Path) -> str:
        authority_repositories.append(repository)
        raise CurrentMainAuthorityError

    monkeypatch.setattr(cli, "current_main_commit", reject_authority)
    # When: activation reaches its authority boundary.
    result = main(
        ("activate", "--config", str(config_path), "--plist", str(plist_path)),
        runner=lambda command: launchctl_calls.append(command) or 0,
    )
    # Then: the guard saw the configured repository and no launchctl command ran.
    assert result == 2
    assert authority_repositories == [config.project_root]
    assert launchctl_calls == []


def test_activate_observes_successful_authority_before_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a verified contract with independently recorded authority and runner events.
    config, config_path, plist_path = _fixture_config(tmp_path)
    assert main(_provision_args(config, config_path, plist_path)) == 0
    events: list[str] = []

    def accept_authority(repository: Path) -> str:
        assert repository == config.project_root
        events.append("authority")
        return "b" * 40

    def runner(command: tuple[str, ...]) -> int:
        events.append(command[1])
        return 0

    monkeypatch.setattr(cli, "current_main_commit", accept_authority)
    # When: activation succeeds.
    result = main(("activate", "--config", str(config_path), "--plist", str(plist_path)), runner=runner)
    # Then: authority completion is observed before the first launchctl operation.
    assert result == 0
    assert events == ["authority", "bootstrap", "kickstart"]
