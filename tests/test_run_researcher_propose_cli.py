from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import run_researcher_propose as researcher_cli
from trading_agent.experiment_ledger_store import ExperimentLedgerReader

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_researcher_propose.py"
SOURCE_EXAMPLE = PROJECT / "examples" / "research" / "us-vwap-reclaim-source-v2.json"


def test_researcher_propose_help_exposes_local_fail_closed_inputs() -> None:
    # Given: the repository CLI entrypoint.
    # When: an operator asks for help.
    completed = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: bounded local generation and append-only evidence inputs are documented.
    assert completed.returncode == 0
    assert "--context" in completed.stdout
    assert "--experiment-ledger" in completed.stdout
    assert "--receipt-root" in completed.stdout
    assert "--manifest-root" in completed.stdout
    assert "--queue-root" in completed.stdout
    assert "--response-fixture" in completed.stdout
    assert "--hermes-executable" in completed.stdout
    assert "--output-dir" in completed.stdout


def test_researcher_propose_rejects_invalid_context_before_creating_ledger(tmp_path: Path) -> None:
    # Given: an invalid context at the CLI trust boundary.
    context = tmp_path / "context.json"
    response = tmp_path / "response.json"
    context.write_text("{}", encoding="utf-8")
    response.write_text("{}", encoding="utf-8")
    ledger = tmp_path / "experiment.sqlite3"

    # When: the local fixture-backed generator is invoked.
    result = researcher_cli.main(_arguments(tmp_path, context, response, ledger))

    # Then: it fails closed before creating proposal or experiment state.
    assert result == 1
    assert not ledger.exists()
    assert not (tmp_path / "receipts").exists()


def test_researcher_propose_fixture_registers_receipt_card_and_queue(tmp_path: Path) -> None:
    # Given: authoritative sources and a deterministic structured model response.
    context, response = _write_cli_fixtures(tmp_path)
    ledger = tmp_path / "experiment.sqlite3"
    artifact_alias = tmp_path / "artifact-alias"
    artifact_alias.symlink_to(tmp_path, target_is_directory=True)

    # When: one bounded proposal attempt runs through hard Critic checks.
    result = researcher_cli.main(_arguments(artifact_alias, context, response, ledger))

    # Then: the LLM receipt precedes preregistration and queue publication.
    reader = ExperimentLedgerReader(ledger)
    assert result == 0
    assert len(reader.research_sources()) == 2
    assert len(reader.research_hypothesis_cards()) == 1
    assert len(tuple((tmp_path / "receipts" / "prompts").glob("*.txt"))) == 1
    assert len(tuple((tmp_path / "receipts" / "responses").glob("*.txt"))) == 1
    assert len(tuple((tmp_path / "receipts" / "calls").glob("*.json"))) == 1
    assert len(tuple((tmp_path / "receipts" / "critiques").glob("*.json"))) == 1
    assert len(tuple((tmp_path / "manifests").glob("research_hypothesis_*.json"))) == 1
    assert len(tuple((tmp_path / "queue").glob("source_hypothesis_queue_*.json"))) == 1
    assert "result: ready" in (tmp_path / "output" / "researcher_propose_ko.md").read_text(encoding="utf-8")


def _arguments(tmp_path: Path, context: Path, response: Path, ledger: Path) -> tuple[str, ...]:
    return (
        "--context",
        str(context),
        "--experiment-ledger",
        str(ledger),
        "--receipt-root",
        str(tmp_path / "receipts"),
        "--manifest-root",
        str(tmp_path / "manifests"),
        "--queue-root",
        str(tmp_path / "queue"),
        "--response-fixture",
        str(response),
        "--output-dir",
        str(tmp_path / "output"),
    )


def _write_cli_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    source_manifest = json.loads(SOURCE_EXAMPLE.read_text(encoding="utf-8"))
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "lane_id": "intraday_momentum",
                "sources": source_manifest["research_sources"],
                "regime_context": "regular_session_high_liquidity",
            }
        ),
        encoding="utf-8",
    )
    response = tmp_path / "response.json"
    response.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "hypothesis_id": source_manifest["experiment_scope"]["hypothesis_id"],
                "hypothesis": source_manifest["hypothesis"],
                "falsification_rule": (
                    "Reject after 20 eligible sessions when profit factor is below 0.75 while the matched "
                    "baseline profit factor is at least 1.0."
                ),
                "cited_source_ids": source_manifest["research_source_ids"],
                "economic_mechanism": source_manifest["economic_mechanism"],
                "counterfactual_baseline": source_manifest["counterfactual_baseline"],
                "strategy_source": "def signal(bars, index):\n    return bars[index]",
                "free_parameters": ["minimum_relative_volume"],
            }
        ),
        encoding="utf-8",
    )
    return context, response
