from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from tests.strategy_research_source_hypothesis_fixtures import append_sources
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_strategy_research_cycle.py"


def test_cli_help_bad_input_and_cycle_store_vertical(tmp_path: Path) -> None:
    # Given: actual cycle-store evidence and clean ledger storage.
    cycle_db = tmp_path / "cycle.sqlite3"
    with ResearchAgentCycleStore(cycle_db) as store:
        source = append_sources(store)
    fixture_now = source.observed_at + dt.timedelta(minutes=1)

    # When: help, malformed, and wiring-only happy paths run through the binary.
    help_result = subprocess.run((sys.executable, str(SCRIPT), "--help"), cwd=PROJECT, capture_output=True, text=True)
    bad = subprocess.run(
        (sys.executable, str(SCRIPT), "--cycle-database", str(cycle_db)),
        cwd=PROJECT,
        capture_output=True,
        text=True,
    )
    happy = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--cycle-database",
            str(cycle_db),
            "--ledger-database",
            str(tmp_path / "ledger.sqlite3"),
            "--evidence-id",
            str(source.evidence_id),
            "--observed-at",
            fixture_now.isoformat(),
            "--fixture-wiring-only",
        ),
        cwd=PROJECT,
        capture_output=True,
        text=True,
    )

    # Then: the safe output contains the full identifier chain without private assessment fields.
    payload = json.loads(happy.stdout)
    identifiers = {
        "source_id",
        "owner",
        "hypothesis_id",
        "protocol_id",
        "attempt_ids",
        "selected_attempt_id",
        "holdout_reveal_id",
        "terminal_result_id",
        "feedback_result_id",
    }
    assert (help_result.returncode, bad.returncode, happy.returncode) == (0, 2, 0)
    assert identifiers <= payload.keys()
    assert payload["evidence_use"] == "wiring_only"
    assert payload["profitability_claim"] is False
    assert "exact_metrics" not in payload
