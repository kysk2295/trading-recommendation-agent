from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import run_planned_intraday_actual_research as cli

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_planned_intraday_actual_research.py"


def test_planned_actual_research_discovers_cumulative_session_candidates(
    tmp_path: Path,
) -> None:
    # Given: prior, required, future, and unrelated children in one session root.
    session_root = tmp_path / "live_sessions"
    prior = session_root / "20260714"
    required = session_root / "20260715"
    future = session_root / "20260716"
    malformed = session_root / "2026714"
    unrelated = session_root / "latest"
    for path in (prior, required, future, malformed, unrelated):
        path.mkdir(parents=True)

    # When: the operator resolves the root for the required session date.
    discovered = cli._resolve_session_dirs(
        (),
        session_root,
        (dt.date(2026, 7, 15),),
    )

    # Then: the plan candidate set accumulates through the required date only.
    assert discovered == (prior.resolve(), required.resolve())


def test_planned_actual_research_cli_exposes_plan_boundary_and_rejects_bad_binding() -> None:
    help_result = subprocess.run(
        (sys.executable, str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    bad_result = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--strategy-binding",
            "invalid",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert help_result.returncode == 0
    assert "--run-key" in help_result.stdout
    assert "--plan-dir" in help_result.stdout
    assert "--queue-dir" in help_result.stdout
    assert "--session-root" in help_result.stdout
    assert "--required-session-date" in help_result.stdout
    assert "--strategy-binding" in help_result.stdout
    assert "--dataset-producer-commit-sha" in help_result.stdout
    assert "--code-version" in help_result.stdout
    assert "--required-outcome-trace-schema-version" in help_result.stdout
    assert "--prerequisite-receipt" in help_result.stdout
    assert "--prerequisite-report" in help_result.stdout
    assert "--prerequisite-wait-until" in help_result.stdout
    assert bad_result.returncode == 2


def test_planned_actual_research_cli_blocks_before_plan_mutation(
    tmp_path: Path,
) -> None:
    plan_dir = tmp_path / "plans"
    queue_dir = tmp_path / "queue"
    ledger = tmp_path / "experiment.sqlite3"
    output_dir = tmp_path / "reports"

    exit_code = cli.main(
        (
            "--run-key",
            "actual-2026-07-23",
            "--plan-dir",
            str(plan_dir),
            "--queue-dir",
            str(queue_dir),
            "--session-dir",
            str(tmp_path / "session"),
            "--required-session-date",
            "2026-07-23",
            "--dataset-dir",
            str(tmp_path / "dataset"),
            "--binding-dir",
            str(tmp_path / "binding"),
            "--entitlement-contract",
            str(tmp_path / "entitlement.json"),
            "--strategy-binding",
            f"vwap_reclaim,actual_vwap_reclaim_forward_v1,{'0' * 64}",
            "--dataset-producer-commit-sha",
            "d" * 40,
            "--code-version",
            "code-version",
            "--required-outcome-trace-schema-version",
            "2",
            "--registered-at",
            "2026-07-23T20:15:00+00:00",
            "--lane-registry",
            str(tmp_path / "lanes.sqlite3"),
            "--experiment-ledger",
            str(ledger),
            "--artifact-root",
            str(tmp_path / "trials"),
            "--review-root",
            str(tmp_path / "reviews"),
            "--output-dir",
            str(output_dir),
            "--prerequisite-receipt",
            str(tmp_path / "missing.receipt"),
            "--prerequisite-report",
            str(tmp_path / "missing-report.md"),
        )
    )

    assert exit_code == 1
    assert not plan_dir.exists()
    assert not queue_dir.exists()
    assert not ledger.exists()
    assert "- result: blocked" in (output_dir / cli.REPORT_NAME).read_text()
