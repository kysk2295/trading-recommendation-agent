from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import run_intraday_research_loop as research_cli
from trading_agent.experiment_ledger_models import TrialEventKind, TrialKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.lane_bootstrap import bootstrap_lane_control_plane
from trading_agent.lane_registry_store import LaneRegistryStore

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_intraday_research_loop.py"
EXAMPLE_MANIFEST = PROJECT / "examples" / "research" / "intraday-challenger-bundle-v1.json"


def test_intraday_research_loop_help_exposes_bounded_local_inputs() -> None:
    # Given: the repository CLI entrypoint.
    # When: an operator asks for help.
    completed = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: the local-only research inputs are documented.
    assert completed.returncode == 0
    assert "--manifest" in completed.stdout
    assert "--input-csv" in completed.stdout
    assert "--lane-registry" in completed.stdout
    assert "--experiment-ledger" in completed.stdout
    assert "--artifact-root" in completed.stdout
    assert "--review-root" in completed.stdout
    assert "--output-dir" in completed.stdout


def test_intraday_research_loop_rejects_invalid_manifest_before_creating_ledgers(tmp_path: Path) -> None:
    # Given: an invalid manifest and otherwise local paths.
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    lane_registry = tmp_path / "lane.sqlite3"
    experiment_ledger = tmp_path / "experiment.sqlite3"
    output = tmp_path / "report"

    # When: the loop is invoked.
    result = research_cli.main(
        (
            "--manifest",
            str(manifest),
            "--input-csv",
            str(PROJECT / "examples" / "example_intraday.csv"),
            "--lane-registry",
            str(lane_registry),
            "--experiment-ledger",
            str(experiment_ledger),
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--review-root",
            str(tmp_path / "reviews"),
            "--output-dir",
            str(output),
        )
    )

    # Then: it fails before any append-only ledger is created.
    assert result == 1
    assert not lane_registry.exists()
    assert not experiment_ledger.exists()
    assert "result: blocked" in (output / "intraday_research_loop_ko.md").read_text(encoding="utf-8")


def test_intraday_research_loop_rejects_a_hypothesis_contract_mismatch(tmp_path: Path) -> None:
    payload = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    payload["hypotheses"][0]["hypothesis_id"] = "H-MOM-WRONG-001"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    lane_registry = tmp_path / "lane.sqlite3"
    _ = bootstrap_lane_control_plane(LaneRegistryStore(lane_registry))
    experiment_ledger = tmp_path / "experiment.sqlite3"

    result = research_cli.main(
        (
            "--manifest",
            str(manifest),
            "--input-csv",
            str(PROJECT / "examples" / "example_intraday.csv"),
            "--lane-registry",
            str(lane_registry),
            "--experiment-ledger",
            str(experiment_ledger),
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--review-root",
            str(tmp_path / "reviews"),
            "--output-dir",
            str(tmp_path / "report"),
        )
    )

    assert result == 1
    assert not experiment_ledger.exists()


def test_intraday_research_loop_runs_and_replays_full_local_vertical(tmp_path: Path) -> None:
    # Given: the canonical lane contracts and an explicit three-challenger hypothesis bundle.
    lane_registry = tmp_path / "lane.sqlite3"
    _ = bootstrap_lane_control_plane(LaneRegistryStore(lane_registry))
    experiment_ledger = tmp_path / "experiment.sqlite3"
    artifacts = tmp_path / "artifacts"
    reviews = tmp_path / "reviews"
    output = tmp_path / "report"
    arguments = (
        "--manifest",
        str(EXAMPLE_MANIFEST),
        "--input-csv",
        str(PROJECT / "examples" / "example_intraday.csv"),
        "--lane-registry",
        str(lane_registry),
        "--experiment-ledger",
        str(experiment_ledger),
        "--artifact-root",
        str(artifacts),
        "--review-root",
        str(reviews),
        "--output-dir",
        str(output),
    )

    # When: the bounded loop and its exact replay are run.
    first = research_cli.main(arguments)
    replay = research_cli.main(arguments)

    # Then: historical trials, terminal evidence, and independent hold decisions are append-only.
    reader = ExperimentLedgerReader(experiment_ledger)
    trials = reader.trials()
    assert first == 0
    assert replay == 0
    assert len(trials) == 3
    assert {row.registration.trial_kind for row in trials} == {TrialKind.HISTORICAL_REPLAY}
    assert all(
        tuple(event.event.event_kind for event in reader.trial_events(row.registration.trial_id))
        == (TrialEventKind.STARTED, TrialEventKind.COMPLETED)
        for row in trials
    )
    artifact_paths = tuple(artifacts.glob("intraday_walk_forward_*.json"))
    review_paths = tuple(reviews.glob("intraday_research_review_*.json"))
    assert len(artifact_paths) == 3
    assert len(review_paths) == 3
    assert {json.loads(path.read_text(encoding="utf-8"))["payload"]["decision"] for path in review_paths} == {
        "hold"
    }
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (*artifact_paths, *review_paths))
    report = (output / "intraday_research_loop_ko.md").read_text(encoding="utf-8")
    assert "result: ready" in report
    assert "external mutation: 0" in report
