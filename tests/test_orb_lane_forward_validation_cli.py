from __future__ import annotations

import csv
import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

import run_orb_lane_forward_validation as cli

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_orb_lane_forward_validation.py"
SESSION_DATE = dt.date(2026, 7, 10)
STARTED_AT = dt.datetime(2026, 7, 10, 20, 5, tzinfo=dt.UTC)
_UV = shutil.which("uv")
assert _UV is not None
UV = Path(_UV)


def test_forward_validation_help_is_executable_and_has_no_authority_options() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0, completed.stderr
    assert "--session-date" in completed.stdout
    assert "--execution-database" in completed.stdout
    assert "--lane-registry" in completed.stdout
    assert "--review-ledger" in completed.stdout
    assert "--output-dir" in completed.stdout
    assert "credential" not in completed.stdout.lower()
    assert "endpoint" not in completed.stdout.lower()
    assert "arm" not in completed.stdout.lower()
    assert "force" not in completed.stdout.lower()
    assert "fixture" not in completed.stdout.lower()


def test_forward_validation_invalid_date_is_argparse_error() -> None:
    completed = subprocess.run(
        (
            str(SCRIPT),
            "missing-session",
            "--session-date",
            "not-a-date",
            "--execution-database",
            "missing-execution",
            "--lane-registry",
            "missing-registry",
            "--review-ledger",
            "missing-review",
            "--output-dir",
            "missing-output",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 2
    assert "YYYY-MM-DD" in completed.stderr


def test_child_commands_are_exact_and_do_not_forward_order_authority(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    snapshot = cli.snapshot_command(paths, SESSION_DATE)
    reviewer = cli.reviewer_command(paths, SESSION_DATE)

    assert snapshot == (
        str(PROJECT / "run_intraday_lane_daily_snapshot.py"),
        str(paths.session),
        "--session-date",
        "2026-07-10",
        "--execution-database",
        str(paths.execution_database),
        "--lane-registry",
        str(paths.lane_registry),
        "--output-dir",
        str(paths.output_dir / "snapshots" / "2026-07-10"),
    )
    assert reviewer == (
        str(PROJECT / "run_lane_reviewer.py"),
        str(paths.session),
        "--session-date",
        "2026-07-10",
        "--lane-registry",
        str(paths.lane_registry),
        "--review-ledger",
        str(paths.review_ledger),
        "--output-dir",
        str(paths.output_dir / "reviews" / "2026-07-10"),
    )
    joined = " ".join((*snapshot, *reviewer)).lower()
    for forbidden in (
        "--arm",
        "--credential",
        "--endpoint",
        "--force",
        "--fixture",
        "paper-api.alpaca.markets",
        "api.alpaca.markets",
        "mutation_smoke",
    ):
        assert forbidden not in joined


def test_snapshot_failure_is_audited_and_does_not_start_reviewer(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...]) -> int:
        calls.append(command)
        return 1

    result = cli.run_forward_validation(
        paths,
        SESSION_DATE,
        runner=run,
        clock=lambda: STARTED_AT,
    )

    assert result.snapshot_exit_code == 1
    assert result.reviewer_exit_code is None
    assert result.completed is False
    assert calls == [cli.snapshot_command(paths, SESSION_DATE)]
    assert _audit_rows(paths.output_dir / cli.SNAPSHOT_AUDIT_NAME) == [(STARTED_AT.isoformat(), "1", "failed")]
    assert not (paths.output_dir / cli.REVIEW_AUDIT_NAME).exists()


def test_success_runs_snapshot_then_reviewer_and_audits_both(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...]) -> int:
        calls.append(command)
        return 0

    result = cli.run_forward_validation(
        paths,
        SESSION_DATE,
        runner=run,
        clock=lambda: STARTED_AT,
    )

    assert result.snapshot_exit_code == 0
    assert result.reviewer_exit_code == 0
    assert result.completed is True
    assert calls == [
        cli.snapshot_command(paths, SESSION_DATE),
        cli.reviewer_command(paths, SESSION_DATE),
    ]
    assert _audit_rows(paths.output_dir / cli.SNAPSHOT_AUDIT_NAME) == [(STARTED_AT.isoformat(), "0", "ok")]
    assert _audit_rows(paths.output_dir / cli.REVIEW_AUDIT_NAME) == [(STARTED_AT.isoformat(), "0", "ok")]


def test_reviewer_failure_is_audited_after_successful_snapshot(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    exit_codes = iter((0, 1))

    result = cli.run_forward_validation(
        paths,
        SESSION_DATE,
        runner=lambda _command: next(exit_codes),
        clock=lambda: STARTED_AT,
    )

    assert result.snapshot_exit_code == 0
    assert result.reviewer_exit_code == 1
    assert result.completed is False
    assert _audit_rows(paths.output_dir / cli.REVIEW_AUDIT_NAME) == [(STARTED_AT.isoformat(), "1", "failed")]


def test_main_replays_same_sequence_and_writes_only_redacted_status(tmp_path: Path) -> None:
    secret = "AKIA_TEST_SECRET_NEVER_REPORT"
    paths = _paths(tmp_path / secret)
    args = _args(paths)
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...]) -> int:
        calls.append(command)
        return 0

    first = cli.main(args, runner=run, clock=lambda: STARTED_AT)
    replay = cli.main(args, runner=run, clock=lambda: STARTED_AT + dt.timedelta(minutes=1))

    assert first == 0
    assert replay == 0
    assert calls == [
        cli.snapshot_command(paths, SESSION_DATE),
        cli.reviewer_command(paths, SESSION_DATE),
        cli.snapshot_command(paths, SESSION_DATE),
        cli.reviewer_command(paths, SESSION_DATE),
    ]
    assert len(_audit_rows(paths.output_dir / cli.SNAPSHOT_AUDIT_NAME)) == 2
    assert len(_audit_rows(paths.output_dir / cli.REVIEW_AUDIT_NAME)) == 2
    report = (paths.output_dir / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "결과: completed" in report
    assert "snapshot phase: success" in report
    assert "Reviewer phase: success" in report
    assert "자동 상태 변경: 금지" in report
    assert "주문 권한 변경: 금지" in report
    assert "외부 Alpaca mutation: 0건" in report
    for forbidden in (
        secret,
        str(paths.session),
        str(paths.execution_database),
        str(paths.lane_registry),
        str(paths.review_ledger),
        "fingerprint",
        "sha256",
        "scope_key",
        "snapshot_key",
        "broker_order_id",
        "payload_json",
    ):
        assert forbidden not in report


def test_main_reports_snapshot_block_without_running_reviewer(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    calls = 0

    def run(_command: tuple[str, ...]) -> int:
        nonlocal calls
        calls += 1
        return 1

    code = cli.main(_args(paths), runner=run, clock=lambda: STARTED_AT)

    assert code == 1
    assert calls == 1
    report = (paths.output_dir / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "결과: blocked" in report
    assert "snapshot phase: failed" in report
    assert "Reviewer phase: not_started" in report


def test_runtime_error_is_fail_closed_and_does_not_start_reviewer(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    calls = 0

    def fail(_command: tuple[str, ...]) -> int:
        nonlocal calls
        calls += 1
        raise OSError("secret subprocess detail")

    code = cli.main(_args(paths), runner=fail, clock=lambda: STARTED_AT)

    assert code == 1
    assert calls == 1
    assert _audit_rows(paths.output_dir / cli.SNAPSHOT_AUDIT_NAME) == [(STARTED_AT.isoformat(), "1", "failed")]
    report = (paths.output_dir / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "secret subprocess detail" not in report
    assert "Reviewer phase: not_started" in report


def _paths(root: Path) -> cli.LaneForwardValidationPaths:
    return cli.LaneForwardValidationPaths(
        session=root / "session",
        execution_database=root / "execution.sqlite3",
        lane_registry=root / "lane_registry.sqlite3",
        review_ledger=root / "review.sqlite3",
        output_dir=root / "control-output",
    )


def _args(paths: cli.LaneForwardValidationPaths) -> list[str]:
    return [
        str(paths.session),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--execution-database",
        str(paths.execution_database),
        "--lane-registry",
        str(paths.lane_registry),
        "--review-ledger",
        str(paths.review_ledger),
        "--output-dir",
        str(paths.output_dir),
    ]


def _audit_rows(path: Path) -> list[tuple[str, str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["started_at", "exit_code", "status"]
    return [tuple(row) for row in rows[1:]]  # type: ignore[misc]


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
