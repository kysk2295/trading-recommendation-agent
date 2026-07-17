from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import run_data_foundation_check as check_cli

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_data_foundation_check.py"
EXAMPLE = PROJECT / "examples" / "data" / "us-orb-data-foundation-v1.json"
REPORT_NAME = "data_foundation_check_ko.md"
UV_PATH = shutil.which("uv")
assert UV_PATH is not None
UV = Path(UV_PATH)


def test_data_foundation_check_help_is_local_and_narrow() -> None:
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
    assert "--output-dir" in completed.stdout
    assert "--database" not in completed.stdout
    assert "--arm" not in completed.stdout


def test_direct_cli_dependency_resolution_is_offline() -> None:
    shebang = SCRIPT.read_text(encoding="utf-8").splitlines()[0]

    assert shebang == "#!/usr/bin/env -S uv run --offline --script"


def test_missing_manifest_is_sanitized_and_blocked(tmp_path: Path) -> None:
    output = tmp_path / "report"

    return_code = check_cli.main(
        (
            "--manifest",
            str(tmp_path / "missing.json"),
            "--output-dir",
            str(output),
        )
    )

    report = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert return_code == 1
    assert "결과: blocked" in report
    assert "contract validation: failed" in report
    assert str(tmp_path) not in report
    assert "network access: 0" in report
    assert "broker mutation: 0" in report
    assert stat.S_IMODE((output / REPORT_NAME).stat().st_mode) == 0o600


def test_fixture_manifest_writes_ready_aggregate_report(tmp_path: Path) -> None:
    output = tmp_path / "report"

    return_code = check_cli.main(
        (
            "--manifest",
            str(EXAMPLE),
            "--output-dir",
            str(output),
        )
    )

    report_path = output / REPORT_NAME
    report = report_path.read_text(encoding="utf-8")
    assert return_code == 0
    assert "결과: ready" in report
    assert "requirement 충족/전체: 1/1" in report
    assert "declared source: 1" in report
    assert "instrument/event: 1/1" in report
    assert "fallback selected: 0" in report
    assert "network access: 0" in report
    assert "broker mutation: 0" in report
    assert str(EXAMPLE) not in report
    assert "fixture-minute-bar" not in report
    assert "spool:fixture" not in report
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_valid_stale_manifest_exits_two_as_blocked_by_data(tmp_path: Path) -> None:
    payload: dict[str, Any] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    payload["capabilities"][0]["latest_event_received_at"] = "2026-07-17T13:59:54Z"
    manifest = tmp_path / "stale.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "report"

    return_code = check_cli.main(
        (
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output),
        )
    )

    report = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert return_code == 2
    assert "결과: blocked_by_data" in report
    assert "requirement 충족/전체: 0/1" in report
    assert "contract validation: passed" in report


def test_contract_check_path_has_no_provider_credential_or_execution_imports() -> None:
    sources = tuple(
        path.read_text(encoding="utf-8")
        for path in (
            PROJECT / "run_data_foundation_check.py",
            PROJECT / "trading_agent" / "data_foundation_manifest.py",
            PROJECT / "trading_agent" / "strategy_data_gate.py",
            PROJECT / "trading_agent" / "data_capability_models.py",
            PROJECT / "trading_agent" / "canonical_event_models.py",
            PROJECT / "trading_agent" / "security_master_models.py",
        )
    )
    forbidden_import_fragments = (
        "import httpx",
        "import websockets",
        "trading_agent.alpaca",
        "trading_agent.kis_",
        "trading_agent.ls_",
        "trading_agent.paper_",
        "trading_agent.*order",
    )

    assert all(
        fragment not in source
        for source in sources
        for fragment in forbidden_import_fragments
    )


def _execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
