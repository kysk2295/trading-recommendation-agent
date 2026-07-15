from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

import run_experiment_ledger_bootstrap as bootstrap_cli
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
)
from trading_agent.lane_registry_store import LaneRegistryStore

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_experiment_ledger_bootstrap.py"
REPORT_NAME = "experiment_ledger_bootstrap_ko.md"
RECORDED_AT = dt.datetime(2026, 7, 15, 20, tzinfo=dt.UTC)
UV = shutil.which("uv")
assert UV is not None
UV_DIRECTORY = Path(UV).parent


def _seed_lane_registry(path: Path) -> None:
    with LaneRegistryStore(path).writer() as writer:
        for manifest in DEFAULT_LANE_MANIFESTS:
            _ = writer.register_manifest(manifest)
        for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES:
            _ = writer.register_experiment_scope(scope)


def test_experiment_ledger_bootstrap_help_is_available() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0
    assert "--database" in completed.stdout
    assert "--lane-registry" in completed.stdout
    assert "--output-dir" in completed.stdout
    assert "--code-version" in completed.stdout


def test_experiment_ledger_bootstrap_unknown_option_exits_before_database_creation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "experiment.sqlite3"
    lane_registry = tmp_path / "lane.sqlite3"
    output = tmp_path / "report"

    completed = subprocess.run(
        (
            str(SCRIPT),
            "--database",
            str(database),
            "--lane-registry",
            str(lane_registry),
            "--output-dir",
            str(output),
            "--code-version",
            "test-code",
            "--unknown-option",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 2
    assert not database.exists()
    assert not lane_registry.exists()
    assert not output.exists()


def test_experiment_ledger_bootstrap_missing_source_is_redacted_and_non_mutating(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sensitive-experiment.sqlite3"
    lane_registry = tmp_path / "sensitive-lane.sqlite3"
    output = tmp_path / "report"

    return_code = bootstrap_cli.main(
        (
            "--database",
            str(database),
            "--lane-registry",
            str(lane_registry),
            "--output-dir",
            str(output),
            "--code-version",
            "test-code",
        ),
        recorded_at=RECORDED_AT,
    )

    assert return_code == 1
    assert not database.exists()
    assert not lane_registry.exists()
    report = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert "결과: blocked" in report
    assert "immutable lane source" in report
    assert str(database) not in report
    assert str(lane_registry) not in report
    assert "외부 broker mutation: 0건" in report


def test_experiment_ledger_bootstrap_happy_path_and_replay_report_only_counts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "experiment.sqlite3"
    lane_registry = tmp_path / "lane.sqlite3"
    output = tmp_path / "report"
    _seed_lane_registry(lane_registry)
    arguments = (
        "--database",
        str(database),
        "--lane-registry",
        str(lane_registry),
        "--output-dir",
        str(output),
        "--code-version",
        "test-code",
    )

    first = bootstrap_cli.main(arguments, recorded_at=RECORDED_AT)
    first_report = (output / REPORT_NAME).read_text(encoding="utf-8")
    replay = bootstrap_cli.main(arguments, recorded_at=RECORDED_AT + dt.timedelta(days=1))
    replay_report = (output / REPORT_NAME).read_text(encoding="utf-8")

    assert first == 0
    assert replay == 0
    assert "hypothesis 신규/재사용: 4/0" in first_report
    assert "strategy version 신규/재사용: 4/0" in first_report
    assert "lifecycle event 신규/재사용: 4/0" in first_report
    assert "hypothesis 신규/재사용: 0/4" in replay_report
    assert "strategy version 신규/재사용: 0/4" in replay_report
    assert "lifecycle event 신규/재사용: 0/4" in replay_report
    assert "state: experimental_shadow" in replay_report
    assert "외부 broker mutation: 0건" in replay_report
    for sensitive in (
        str(database),
        str(lane_registry),
        "registration_key",
        "event_key",
        "experiment_scope_key",
        "test-code",
    ):
        assert sensitive not in first_report
        assert sensitive not in replay_report


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV_DIRECTORY}:/usr/bin:/bin"
    return environment
