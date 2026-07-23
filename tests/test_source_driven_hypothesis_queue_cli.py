from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import run_source_driven_hypothesis_queue as queue_cli
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.research_hypothesis_registration import register_research_hypothesis_manifest

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_source_driven_hypothesis_queue.py"
EXAMPLE = PROJECT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
REPORT_NAME = "source_driven_hypothesis_queue_ko.md"
UV_PATH = shutil.which("uv")
assert UV_PATH is not None
UV = Path(UV_PATH)


def test_source_driven_hypothesis_queue_help_is_available() -> None:
    completed = subprocess.run(
        (str(UV), "run", "python", str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_execution_environment(),
    )

    assert completed.returncode == 0
    assert "--database" in completed.stdout
    assert "--artifact-root" in completed.stdout
    assert "--output-dir" in completed.stdout


def test_source_driven_hypothesis_queue_missing_ledger_is_blocked(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    output = tmp_path / "report"

    return_code = queue_cli.main(
        (
            "--database",
            str(tmp_path / "missing.sqlite3"),
            "--artifact-root",
            str(artifact_root),
            "--output-dir",
            str(output),
        )
    )

    report = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert return_code == 1
    assert not artifact_root.exists()
    assert "결과: blocked" in report
    assert "external mutation: 0" in report


def test_source_driven_hypothesis_queue_happy_path_replays_exact_artifact(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    _ = register_research_hypothesis_manifest(EXAMPLE, ExperimentLedgerStore(database))
    artifact_root = tmp_path / "artifacts"
    output = tmp_path / "report"
    arguments = (
        "--database",
        str(database),
        "--artifact-root",
        str(artifact_root),
        "--output-dir",
        str(output),
    )

    first = queue_cli.main(arguments)
    first_report = (output / REPORT_NAME).read_text(encoding="utf-8")
    replay = queue_cli.main(arguments)
    replay_report = (output / REPORT_NAME).read_text(encoding="utf-8")
    artifacts = tuple(artifact_root.glob("source_hypothesis_queue_*.json"))

    assert first == 0
    assert replay == 0
    assert "queue item: 1" in first_report
    assert "strategy design: 1" in first_report
    assert "queue artifact 신규/재사용: 1/0" in first_report
    assert "queue artifact 신규/재사용: 0/1" in replay_report
    assert "lifecycle authority: false" in replay_report
    assert "allocation authority: false" in replay_report
    assert "order authority: false" in replay_report
    assert len(artifacts) == 1
    assert stat.S_IMODE(artifacts[0].stat().st_mode) == 0o600
    assert stat.S_IMODE((output / REPORT_NAME).stat().st_mode) == 0o600


def test_source_driven_hypothesis_queue_path_has_no_provider_or_broker_imports() -> None:
    sources = (
        SCRIPT.read_text(encoding="utf-8"),
        (PROJECT / "trading_agent" / "source_driven_hypothesis_queue.py").read_text(encoding="utf-8"),
    )

    assert all("alpaca" not in source.lower() for source in sources)
    assert all("paper_" not in source.lower() for source in sources)
    assert all("kis_" not in source.lower() for source in sources)
    assert all("ls_" not in source.lower() for source in sources)


def _execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
