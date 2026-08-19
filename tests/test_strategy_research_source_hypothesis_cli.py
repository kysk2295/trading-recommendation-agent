from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.strategy_research_source_hypothesis_fixtures import NOW, append_sources
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_strategy_research_source_hypothesis.py"


def test_source_hypothesis_cli_help_exposes_read_only_store_inputs() -> None:
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
    assert all(option in completed.stdout for option in ("--cycle-database", "--evidence-id", "--observed-at"))
    assert not any(option in completed.stdout for option in ("--order", "--account", "--position"))


def test_source_hypothesis_cli_drives_production_factory_from_cycle_store(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "cycle.sqlite3"
    with ResearchAgentCycleStore(database) as store:
        source = append_sources(store)

    # When
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--cycle-database",
            str(database),
            "--evidence-id",
            str(source.evidence_id),
            "--observed-at",
            NOW.isoformat(),
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert payload["source_id"] == "ranking:nas:1:acme"
    assert payload["owner"] == "intraday_momentum"
    assert payload["source_id"] in payload["artifact_refs"]
    assert payload["observation_id"] in payload["artifact_refs"]
    assert payload["hypothesis_id"] in payload["artifact_refs"]
    assert payload["trading_authority"] is False


def test_source_hypothesis_cli_rejects_malformed_id_nonzero(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "cycle.sqlite3"
    with ResearchAgentCycleStore(database) as store:
        _ = append_sources(store)

    # When
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--cycle-database",
            str(database),
            "--evidence-id",
            "malformed",
            "--observed-at",
            NOW.isoformat(),
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "broker_mutation": 0,
        "model_calls": 0,
        "status": "invalid",
    }
