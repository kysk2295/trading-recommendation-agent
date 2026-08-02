from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from tests.research_agent_primary_fixtures import (
    NOW,
    seed_day,
    seed_market_context,
    seed_opportunity,
    source_paths,
    write_service_config,
)

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_research_agent_primary_sources.py"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(SCRIPT), *arguments),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_help_exposes_read_only_deterministic_inspection_surface() -> None:
    # Given / When
    completed = _run("--help")

    # Then
    assert completed.returncode == 0
    assert all(option in completed.stdout for option in ("inspect", "--config", "--now"))
    assert not any(option in completed.stdout for option in ("--submit-order", "--account", "--positions"))


def test_documented_uv_script_help_runs_with_declared_dependencies() -> None:
    # Given
    uv = shutil.which("uv")
    assert uv is not None

    # When
    completed = subprocess.run(
        (uv, "run", "--script", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    assert "inspect" in completed.stdout


def test_bad_now_is_redacted_and_rejected_before_source_inspection(tmp_path: Path) -> None:
    # Given
    paths = source_paths(tmp_path)
    config = write_service_config(tmp_path, paths)

    # When
    completed = _run("inspect", "--config", str(config), "--now", "not-a-time")

    # Then
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {"broker_mutation": 0, "status": "invalid"}
    assert str(tmp_path) not in completed.stderr


def test_inspection_reports_exact_primary_families_and_redacted_digests(tmp_path: Path) -> None:
    # Given
    paths = source_paths(tmp_path)
    seed_opportunity(paths)
    seed_market_context(paths)
    seed_day(paths)
    config = write_service_config(tmp_path, paths)

    # When
    completed = _run("inspect", "--config", str(config), "--now", NOW.isoformat())

    # Then
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["broker_mutation"] == 0
    assert payload["provider_calls"] == 0
    assert payload["status"] == "ready"
    assert [item["agent_family_id"] for item in payload["families"]] == [
        "opportunity_manager",
        "market_context",
        "day_trading",
    ]
    assert all(item["status"] == "ready" for item in payload["families"])
    assert all(len(item["provenance_sha256"]) >= 1 for item in payload["families"])
    assert str(tmp_path) not in completed.stdout


def test_inspection_reports_closed_stale_and_missing_spread_without_mutation(tmp_path: Path) -> None:
    # Given
    paths = source_paths(tmp_path)
    seed_opportunity(paths, spread=None)
    seed_market_context(paths, valid_until=NOW - NOW.resolution)
    seed_day(paths)
    config = write_service_config(tmp_path, paths)

    # When
    completed = _run("inspect", "--config", str(config), "--now", NOW.isoformat())

    # Then
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["broker_mutation"] == payload["provider_calls"] == 0
    assert payload["status"] == "blocked"
    assert [item["source_key"] for item in payload["families"]] == [
        "opportunity.blocked.missing_spread",
        "market_context.blocked.stale",
        "day.session.20260803",
    ]
