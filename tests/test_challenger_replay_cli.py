from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

from pydantic import TypeAdapter

from tests.challenger_replay_fixtures import write_closed_source_session


class ReplayGateJson(TypedDict):
    strategy: str
    passed: bool
    comparison_eligible: bool
    reasons: list[str]
    input_snapshots: int
    complete_symbols: int
    censored_symbols: int
    recommendations: int
    completed_trades: int


GATE_ADAPTER = TypeAdapter(ReplayGateJson)


def test_closed_causal_source_replays_gap_challenger_in_isolated_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "gap_and_go"
    write_closed_source_session(source)

    completed = _run(source, output)

    assert completed.returncode == 0, completed.stderr
    gate = GATE_ADAPTER.validate_json((output / "challenger_replay_gate.json").read_text(encoding="utf-8"))
    assert gate == {
        "strategy": "gap_and_go",
        "passed": True,
        "comparison_eligible": False,
        "reasons": ["portfolio_comparison_not_implemented"],
        "input_snapshots": 2,
        "complete_symbols": 1,
        "censored_symbols": 1,
        "recommendations": 1,
        "completed_trades": 1,
    }
    with sqlite3.connect(output / "paper_recommendations.sqlite3") as connection:
        row: tuple[str, str, str] | None = connection.execute(
            "SELECT symbol, strategy, created_at FROM recommendations"
        ).fetchone()
    assert row is not None
    assert row[0:2] == ("DEMO", "five_minute_gap_hold")
    assert row[2] == "2026-07-14T09:35:30-04:00"
    coverage = (output / "symbol_coverage.csv").read_text(encoding="utf-8")
    assert "DEMO,390,390,True," in coverage
    assert "SHORT,390,5,False,missing_regular_minutes" in coverage
    metrics = (output / "paper_metrics" / "paper_metrics.csv").read_text(encoding="utf-8")
    assert "20,1," in metrics
    assert not (source / "challenger_replay_gate.json").exists()


def test_replay_fails_closed_before_writing_strategy_db_when_close_proof_is_missing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "gap_and_go"
    write_closed_source_session(source, post_session_complete=False)

    completed = _run(source, output)

    assert completed.returncode == 2
    gate = json.loads((output / "challenger_replay_gate.json").read_text(encoding="utf-8"))
    assert gate["passed"] is False
    assert "post_session_metrics_missing_or_failed" in gate["reasons"]
    assert not (output / "paper_recommendations.sqlite3").exists()
    assert not (output / "paper_metrics").exists()


def test_replay_rejects_candidate_context_that_points_to_an_unfinished_bar(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "gap_and_go"
    write_closed_source_session(source, include_censored_symbol=False)
    with sqlite3.connect(source / "paper_recommendations.sqlite3") as connection:
        _ = connection.execute("UPDATE candidate_input_snapshots SET latest_completed_bar_at = observed_at")

    completed = _run(source, output)

    assert completed.returncode == 2
    gate = json.loads((output / "challenger_replay_gate.json").read_text(encoding="utf-8"))
    assert "candidate_input_uses_unfinished_bar" in gate["reasons"]
    assert not (output / "paper_recommendations.sqlite3").exists()


def test_missing_source_database_is_rejected_without_creating_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "gap_and_go"
    source.mkdir()

    completed = _run(source, output)

    assert completed.returncode == 2
    gate = json.loads((output / "challenger_replay_gate.json").read_text(encoding="utf-8"))
    assert gate["reasons"] == ["source_database_missing"]
    assert not (source / "paper_recommendations.sqlite3").exists()
    assert not (output / "paper_recommendations.sqlite3").exists()


def _run(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    project = Path(__file__).parents[1]
    return subprocess.run(
        (
            sys.executable,
            str(project / "run_shadow_challenger_replay.py"),
            str(source),
            "--strategy",
            "gap_and_go",
            "--output-dir",
            str(output),
        ),
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
