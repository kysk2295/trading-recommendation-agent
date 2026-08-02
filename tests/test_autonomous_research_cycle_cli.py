from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import run_autonomous_research_cycle as cycle_cli

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_autonomous_research_cycle.py"
CONTEXT = PROJECT / "examples" / "research" / "researcher-context-v1.json"
RESPONSE = PROJECT / "examples" / "research" / "researcher-response-fixture-v1.json"
INPUT = PROJECT / "examples" / "example_intraday.csv"
FOUNDATION = PROJECT / "examples" / "data" / "us-vwap-reclaim-historical-fixture-v1.json"


def test_autonomous_cycle_help_exposes_bounded_local_inputs() -> None:
    # Given: the public one-shot autonomous cycle CLI.
    # When: an operator requests its help surface.
    completed = subprocess.run(
        ("uv", "run", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: generation, sandbox, data, experiment, review, and output boundaries are explicit.
    assert completed.returncode == 0
    for option in (
        "--context",
        "--experiment-ledger",
        "--strategy-root",
        "--python-executable",
        "--input-csv",
        "--data-foundation-manifest",
        "--artifact-root",
        "--review-root",
        "--response-fixture",
        "--hermes-executable",
    ):
        assert option in completed.stdout


def test_autonomous_cycle_rejects_unbound_python_before_ledger_creation(tmp_path: Path) -> None:
    # Given: an executable that cannot satisfy the bound Python inventory contract.
    ledger = tmp_path / "experiment.sqlite3"

    # When: the cycle is invoked with that substituted runtime.
    result = cycle_cli.main(_arguments(tmp_path, ledger, Path("/bin/false")))

    # Then: execution is blocked before research state or generated source is created.
    assert result == 1
    assert not ledger.exists()
    assert not (tmp_path / "strategies").exists()
    report = (tmp_path / "output" / "autonomous_research_cycle_ko.md").read_text(
        encoding="utf-8"
    )
    assert "result: blocked" in report
    assert "trading mutation: 0" in report
    parsed = cycle_cli.load_autonomous_cycle_cli_result(tmp_path / "output")
    assert parsed.status == "blocked"
    assert parsed.reason_codes == ("cycle_or_evidence_invalid",)


def test_autonomous_cycle_fixture_completes_without_broker_mutation(tmp_path: Path) -> None:
    # Given: local source evidence, fixture model output, bars, and data foundation.
    ledger = tmp_path / "experiment.sqlite3"

    # When: one complete bounded cycle runs through the real CLI surface.
    result = cycle_cli.main(_arguments(tmp_path, ledger, Path(sys.executable)))

    # Then: one strategy, experiment, review, and private zero-mutation report are observable.
    assert result == 0
    assert ledger.is_file()
    assert len(tuple((tmp_path / "strategies").glob("*/strategy.py"))) == 1
    assert len(tuple((tmp_path / "experiments").glob("*.json"))) == 1
    assert len(tuple((tmp_path / "reviews").glob("*.json"))) == 1
    report = (tmp_path / "output" / "autonomous_research_cycle_ko.md").read_text(
        encoding="utf-8"
    )
    assert "result: complete" in report
    assert "reviewer_decision: hold" in report
    assert "trading mutation: 0" in report
    assert "strategy_source" not in report
    parsed = cycle_cli.load_autonomous_cycle_cli_result(tmp_path / "output")
    assert parsed.status == "complete"
    assert parsed.reviewer_decision == "hold"
    assert parsed.trading_mutation == 0


def _arguments(tmp_path: Path, ledger: Path, python: Path) -> tuple[str, ...]:
    return (
        "--context",
        str(CONTEXT),
        "--response-fixture",
        str(RESPONSE),
        "--experiment-ledger",
        str(ledger),
        "--receipt-root",
        str(tmp_path / "receipts"),
        "--strategy-root",
        str(tmp_path / "strategies"),
        "--manifest-root",
        str(tmp_path / "manifests"),
        "--queue-root",
        str(tmp_path / "queue"),
        "--input-csv",
        str(INPUT),
        "--data-foundation-manifest",
        str(FOUNDATION),
        "--artifact-root",
        str(tmp_path / "experiments"),
        "--review-root",
        str(tmp_path / "reviews"),
        "--output-dir",
        str(tmp_path / "output"),
        "--python-executable",
        str(python),
        "--max-bars",
        "10",
        "--max-sessions",
        "1",
    )
