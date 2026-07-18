from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import run_data_capability_registry as registry_cli

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_data_capability_registry.py"
EXAMPLE = PROJECT / "examples" / "data" / "us-orb-data-foundation-v1.json"
UV_PATH = shutil.which("uv")
assert UV_PATH is not None
UV = Path(UV_PATH)


def test_registry_cli_help_is_local_and_has_no_execution_controls() -> None:
    completed = subprocess.run(
        (str(UV), "run", "python", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_execution_environment(),
    )

    assert completed.returncode == 0
    assert "--manifest" in completed.stdout
    assert "--database" in completed.stdout
    assert "--output-dir" in completed.stdout
    assert "--arm" not in completed.stdout
    assert "credential" not in completed.stdout


def test_missing_manifest_blocks_before_database_creation(tmp_path: Path) -> None:
    database = tmp_path / "registry.sqlite3"
    output = tmp_path / "report"

    result = registry_cli.main(
        (
            "--manifest",
            str(tmp_path / "missing.json"),
            "--database",
            str(database),
            "--output-dir",
            str(output),
        )
    )

    report = (output / registry_cli.REPORT_NAME).read_text()
    assert result == 1
    assert not database.exists()
    assert "결과: blocked" in report
    assert str(tmp_path) not in report


def test_ready_manifest_appends_once_and_replays_registry_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "registry.sqlite3"
    output = tmp_path / "report"
    arguments = (
        "--manifest",
        str(EXAMPLE),
        "--database",
        str(database),
        "--output-dir",
        str(output),
    )

    first = registry_cli.main(arguments)
    first_report = (output / registry_cli.REPORT_NAME).read_text()
    second = registry_cli.main(arguments)
    second_report_path = output / registry_cli.REPORT_NAME
    second_report = second_report_path.read_text()

    assert first == second == 0
    assert "결과: ready" in first_report
    assert "capability appended: 1" in first_report
    assert "entitlement appended: 1" in first_report
    assert "capability appended: 0" in second_report
    assert "entitlement appended: 0" in second_report
    assert "capability resolved: 1/1" in second_report
    assert "entitlement resolved: 1/1" in second_report
    assert str(EXAMPLE) not in second_report
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(second_report_path.stat().st_mode) == 0o600


def test_registry_cli_does_not_import_provider_or_execution_modules() -> None:
    sources = tuple(
        path.read_text()
        for path in (
            SCRIPT,
            PROJECT / "trading_agent" / "data_capability_registry.py",
            PROJECT / "trading_agent" / "data_capability_registry_support.py",
        )
    )
    forbidden = (
        "import httpx",
        "import websockets",
        "trading_agent.alpaca_http",
        "trading_agent.paper_",
        "trading_agent.*order",
    )

    assert all(fragment not in source for source in sources for fragment in forbidden)


def _execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
