from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.research_agent_primary_fixtures import write_service_config
from tests.research_agent_research_source_fixtures import NOW, populated_source_paths

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_research_agent_research_sources.py"


def test_help_exposes_only_read_only_research_inspection() -> None:
    # Given / When
    completed = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0
    assert all(option in completed.stdout for option in ("inspect", "--config", "--now"))
    assert not any(option in completed.stdout for option in ("--submit-order", "--account", "--positions"))


def test_inspect_reports_ready_research_families_without_paths_or_effects(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    config = write_service_config(tmp_path, paths)

    completed = subprocess.run(
        (sys.executable, str(SCRIPT), "inspect", "--config", str(config), "--now", NOW.isoformat()),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert payload["status"] == "ready"
    assert [item["agent_family_id"] for item in payload["families"]] == [
        "swing_trading",
        "systematic_quant",
        "derivatives_research",
    ]
    assert all(
        payload[counter] == 0 for counter in ("provider_calls", "model_calls", "heavy_processes", "broker_mutation")
    )
    assert str(tmp_path) not in completed.stdout


def test_bad_now_and_nonprivate_config_are_rejected_without_path_disclosure(tmp_path: Path) -> None:
    paths = populated_source_paths(tmp_path)
    config = write_service_config(tmp_path, paths)

    bad_now = subprocess.run(
        (sys.executable, str(SCRIPT), "inspect", "--config", str(config), "--now", "not-a-time"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    config.chmod(0o644)
    nonprivate = subprocess.run(
        (sys.executable, str(SCRIPT), "inspect", "--config", str(config), "--now", NOW.isoformat()),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    expected = {"broker_mutation": 0, "heavy_processes": 0, "model_calls": 0, "provider_calls": 0, "status": "invalid"}
    assert all(completed.returncode == 2 and completed.stdout == "" for completed in (bad_now, nonprivate))
    assert all(json.loads(completed.stderr) == expected for completed in (bad_now, nonprivate))
    assert str(tmp_path) not in bad_now.stderr + nonprivate.stderr
