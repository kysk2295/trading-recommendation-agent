from __future__ import annotations

import datetime as dt
import plistlib
import stat
import subprocess
from pathlib import Path

import pytest

import run_us_news_catalyst_day_service as service_cli
from tests.us_news_catalyst_trial_fixtures import PROJECT, REGISTRATION_MANIFEST, registered_ledger
from trading_agent.us_news_catalyst_day_service import (
    UsNewsCatalystDayServiceLeaseUnavailableError,
    UsNewsCatalystDayServiceRuntime,
    UsNewsCatalystDayServiceStatus,
    run_us_news_catalyst_day_service_tick,
)
from trading_agent.us_news_catalyst_day_service_config import (
    InvalidUsNewsCatalystDayServiceError,
    UsNewsCatalystDayServiceConfig,
    load_us_news_catalyst_day_service_config,
    verify_us_news_catalyst_launch_agent,
)

UTC = dt.UTC
UV = Path(subprocess.run(("which", "uv"), check=True, capture_output=True, text=True).stdout.strip())


def test_service_provision_writes_private_secret_free_launch_agent(tmp_path: Path) -> None:
    # Given
    config_path = tmp_path / "private" / "service.json"
    plist_path = tmp_path / "private" / "com.example.us-news.plist"
    output = tmp_path / "reports"

    # When
    exit_code = service_cli.main(
        _provision_args(tmp_path, config_path, plist_path, output),
    )

    # Then
    assert exit_code == 0
    config = load_us_news_catalyst_day_service_config(config_path)
    payload = plistlib.loads(plist_path.read_bytes())
    assert config.label == "com.example.us-news"
    assert payload["StartInterval"] == 30
    assert payload["RunAtLoad"] is True
    assert payload["ProgramArguments"][-4:] == [
        "--config",
        str(config_path),
        "--output-dir",
        str(config.output_root / "service"),
    ]
    assert "EnvironmentVariables" not in payload
    assert "KeepAlive" not in payload
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600
    assert verify_us_news_catalyst_launch_agent(config_path, plist_path).ready is True


def test_service_tick_bootstraps_preopen_once_then_reuses_manifest(tmp_path: Path) -> None:
    # Given
    config = _config(tmp_path)
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> int:
        commands.append(command)
        if "init" in command:
            manifest_index = command.index("--manifest") + 1
            Path(command[manifest_index]).parent.mkdir(parents=True, exist_ok=True)
            return _run_day_init(command, dt.datetime(2026, 7, 21, 12, tzinfo=UTC))
        return 0

    runtime = UsNewsCatalystDayServiceRuntime(runner=runner)
    observed = dt.datetime(2026, 7, 21, 12, tzinfo=UTC)

    # When
    first = run_us_news_catalyst_day_service_tick(config, observed, runtime)
    second = run_us_news_catalyst_day_service_tick(config, observed + dt.timedelta(seconds=30), runtime)

    # Then
    assert first.status is UsNewsCatalystDayServiceStatus.INITIALIZED
    assert second.status is UsNewsCatalystDayServiceStatus.TICKED
    assert sum("init" in command for command in commands) == 1
    assert sum("tick" in command for command in commands) == 2
    assert first.manifest_path is not None
    assert first.manifest_path == second.manifest_path
    assert first.manifest_path.is_file()


def test_service_tick_fails_closed_when_manifest_is_missing_after_open(tmp_path: Path) -> None:
    # Given
    config = _config(tmp_path)
    commands: list[tuple[str, ...]] = []
    runtime = UsNewsCatalystDayServiceRuntime(runner=lambda command: commands.append(command) or 0)

    # When
    result = run_us_news_catalyst_day_service_tick(
        config,
        dt.datetime(2026, 7, 21, 14, tzinfo=UTC),
        runtime,
    )

    # Then
    assert result.status is UsNewsCatalystDayServiceStatus.BLOCKED
    assert result.reason_code == "bootstrap_window_missed"
    assert commands == []


def test_service_tick_rejects_a_duplicate_process_while_writer_lease_is_held(
    tmp_path: Path,
) -> None:
    # Given
    config = _config(tmp_path)
    observed = dt.datetime(2026, 7, 21, 12, tzinfo=UTC)
    duplicate_blocked = False

    def runner(command: tuple[str, ...]) -> int:
        nonlocal duplicate_blocked
        if not duplicate_blocked:
            with pytest.raises(UsNewsCatalystDayServiceLeaseUnavailableError):
                _ = run_us_news_catalyst_day_service_tick(config, observed, UsNewsCatalystDayServiceRuntime(runner))
            duplicate_blocked = True
        if "init" in command:
            return _run_day_init(command, observed)
        return 0

    # When
    result = run_us_news_catalyst_day_service_tick(config, observed, UsNewsCatalystDayServiceRuntime(runner))

    # Then
    assert result.status is UsNewsCatalystDayServiceStatus.INITIALIZED
    assert duplicate_blocked is True


def test_service_tick_waits_on_non_session_day_without_commands(tmp_path: Path) -> None:
    # Given
    config = _config(tmp_path)
    commands: list[tuple[str, ...]] = []
    runtime = UsNewsCatalystDayServiceRuntime(runner=lambda command: commands.append(command) or 0)

    # When
    result = run_us_news_catalyst_day_service_tick(
        config,
        dt.datetime(2026, 7, 19, 14, tzinfo=UTC),
        runtime,
    )

    # Then
    assert result.status is UsNewsCatalystDayServiceStatus.WAITING
    assert result.reason_code == "non_session_day"
    assert commands == []


def test_launch_agent_verifier_rejects_world_readable_plist(tmp_path: Path) -> None:
    # Given
    config_path = tmp_path / "private" / "service.json"
    plist_path = tmp_path / "private" / "com.example.us-news.plist"
    assert service_cli.main(_provision_args(tmp_path, config_path, plist_path, tmp_path / "reports")) == 0
    plist_path.chmod(0o644)

    # When / Then
    with pytest.raises(InvalidUsNewsCatalystDayServiceError):
        _ = verify_us_news_catalyst_launch_agent(config_path, plist_path)


def test_service_cli_help_and_bad_config_are_safe(tmp_path: Path) -> None:
    # Given / When
    help_result = subprocess.run(
        ("uv", "run", "python", "run_us_news_catalyst_day_service.py", "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    output = tmp_path / "reports"
    blocked = service_cli.main(
        ("tick", "--config", str(tmp_path / "private-name.json"), "--output-dir", str(output)),
    )

    # Then
    assert help_result.returncode == 0
    assert {"provision", "tick", "verify"} <= set(help_result.stdout.split())
    assert blocked == 1
    report = (output / service_cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: blocked" in report
    assert "private-name" not in report


def test_service_cli_unsafe_output_path_fails_without_traceback(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "real-output"
    target.mkdir()
    linked_output = tmp_path / "linked-output"
    linked_output.symlink_to(target, target_is_directory=True)

    # When
    result = subprocess.run(
        (
            "uv",
            "run",
            "python",
            "run_us_news_catalyst_day_service.py",
            "tick",
            "--config",
            str(tmp_path / "missing.json"),
            "--output-dir",
            str(linked_output),
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert result.returncode == 1
    assert result.stderr == ""


def _config(tmp_path: Path) -> UsNewsCatalystDayServiceConfig:
    return UsNewsCatalystDayServiceConfig(
        label="com.example.us-news",
        project_root=PROJECT,
        uv_path=UV,
        registration_manifest=REGISTRATION_MANIFEST,
        experiment_ledger=registered_ledger(tmp_path).path,
        projection_root=(tmp_path / "projections").absolute(),
        evidence_root=(tmp_path / "evidence").absolute(),
        security_master_store=(tmp_path / "security.sqlite3").absolute(),
        session_root=(tmp_path / "sessions").absolute(),
        output_root=(tmp_path / "runtime-reports").absolute(),
        secret_path=(tmp_path / "alpaca.env").absolute(),
    )


def _provision_args(
    tmp_path: Path,
    config_path: Path,
    plist_path: Path,
    output: Path,
) -> tuple[str, ...]:
    return (
        "provision",
        "--label",
        "com.example.us-news",
        "--project-root",
        str(PROJECT),
        "--uv-path",
        str(UV),
        "--registration-manifest",
        str(REGISTRATION_MANIFEST),
        "--experiment-ledger",
        str(registered_ledger(tmp_path).path),
        "--projection-root",
        str(tmp_path / "projections"),
        "--evidence-root",
        str(tmp_path / "evidence"),
        "--security-master-store",
        str(tmp_path / "security.sqlite3"),
        "--session-root",
        str(tmp_path / "sessions"),
        "--runtime-output-root",
        str(tmp_path / "runtime-reports"),
        "--secret-path",
        str(tmp_path / "alpaca.env"),
        "--config",
        str(config_path),
        "--plist",
        str(plist_path),
        "--output-dir",
        str(output),
    )


def _run_day_init(command: tuple[str, ...], created_at: dt.datetime) -> int:
    from run_us_news_catalyst_day_session import main as day_main

    script_index = command.index("run_us_news_catalyst_day_session.py")
    return day_main(command[script_index + 1 :], clock=lambda: created_at)
