from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import run_planned_intraday_actual_research as cli
from trading_agent.private_report import write_private_report

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_planned_intraday_actual_research.py"


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
    assert "--required-session-date" in help_result.stdout
    assert "--strategy-binding" in help_result.stdout
    assert "--dataset-producer-commit-sha" in help_result.stdout
    assert "--code-version" in help_result.stdout
    assert "--required-outcome-trace-schema-version" in help_result.stdout
    assert "--prerequisite-receipt" in help_result.stdout
    assert "--prerequisite-report" in help_result.stdout
    assert bad_result.returncode == 2


def test_planned_actual_research_prerequisite_requires_both_paths(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "closeout.receipt"

    with pytest.raises(ValueError, match="prerequisite_paths_incomplete"):
        cli._require_closeout_prerequisite(receipt, None)


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


@pytest.mark.parametrize(
    ("receipt_payload", "report_result"),
    (
        ("exit_code=1\ncompleted_at_epoch=1784862960\n", "recovered"),
        ("exit_code=0\ncompleted_at_epoch=1784862960\n", "blocked"),
    ),
)
def test_planned_actual_research_prerequisite_blocks_failed_closeout(
    tmp_path: Path,
    receipt_payload: str,
    report_result: str,
) -> None:
    receipt = tmp_path / "closeout.receipt"
    report = tmp_path / "forward_post_session_closeout_ko.md"
    write_private_report(receipt, receipt_payload)
    write_private_report(report, _closeout_report(report_result))

    with pytest.raises(ValueError, match="closeout_prerequisite_invalid"):
        cli._require_closeout_prerequisite(receipt, report)


@pytest.mark.parametrize("result", ("recovered", "replayed"))
def test_planned_actual_research_prerequisite_accepts_strict_closeout(
    tmp_path: Path,
    result: str,
) -> None:
    receipt = tmp_path / "closeout.receipt"
    report = tmp_path / "forward_post_session_closeout_ko.md"
    write_private_report(
        receipt,
        "exit_code=0\ncompleted_at_epoch=1784862960\n",
    )
    write_private_report(report, _closeout_report(result))

    cli._require_closeout_prerequisite(receipt, report)


@pytest.mark.parametrize(
    ("minimum_watch_cycles", "ranking_cycles"),
    ((1, 300), (300, 299)),
)
def test_planned_actual_research_prerequisite_rejects_relaxed_closeout(
    tmp_path: Path,
    minimum_watch_cycles: int,
    ranking_cycles: int,
) -> None:
    receipt = tmp_path / "closeout.receipt"
    report = tmp_path / "forward_post_session_closeout_ko.md"
    write_private_report(
        receipt,
        "exit_code=0\ncompleted_at_epoch=1784862960\n",
    )
    write_private_report(
        report,
        _closeout_report(
            "recovered",
            minimum_watch_cycles=minimum_watch_cycles,
            ranking_cycles=ranking_cycles,
        ),
    )

    with pytest.raises(ValueError, match="closeout_prerequisite_invalid"):
        cli._require_closeout_prerequisite(receipt, report)


def _closeout_report(
    result: str,
    *,
    minimum_watch_cycles: int = 300,
    ranking_cycles: int = 300,
) -> str:
    return (
        "# Forward post-session strict closeout\n\n"
        f"- result: {result}\n"
        f"- minimum watch cycles: {minimum_watch_cycles}\n"
        "- watch cycles: 300\n"
        f"- ranking cycles: {ranking_cycles}\n"
        "- retry cycles: 300\n"
        "- candidate input cycles: 300\n"
        "- failed cycle deletion: 0\n"
        "- quality gate relaxed: false\n"
        "- provider, credential, account, or order operation: 0\n"
    )
