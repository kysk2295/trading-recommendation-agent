from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

import run_orb_forward_trial as trial_cli
from tests.test_orb_forward_trial import (
    AFTER_CLOSE,
    CODE_VERSION,
    OPEN,
    ORB_CONTRACT,
    PREOPEN,
    SESSION_DATE,
    _seed_lineage,
    _seed_started_trial,
    _seed_terminal_sources,
)
from trading_agent.orb_forward_trial import OrbTrialFailurePhase
from trading_agent.scan_cycle import append_cycle_audit

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_orb_forward_trial.py"
REPORT_NAME = "orb_forward_trial_ko.md"
UV = shutil.which("uv")
assert UV is not None
UV_DIRECTORY = Path(UV).parent


def test_help_exposes_local_trial_operations_without_authority_options() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0
    for operation in ("register", "start", "finalize", "fail"):
        assert operation in completed.stdout
    for forbidden in ("--credential", "--endpoint", "--arm", "--force", "--strategy"):
        assert forbidden not in completed.stdout


def test_unknown_option_creates_no_source_or_output(tmp_path: Path) -> None:
    completed = subprocess.run(
        (
            str(SCRIPT),
            "register",
            *_register_arguments(tmp_path),
            "--unknown-option",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 2
    assert not (tmp_path / "experiment.sqlite3").exists()
    assert not (tmp_path / "lane.sqlite3").exists()
    assert not (tmp_path / "report").exists()


def test_missing_register_sources_are_blocked_without_creating_databases(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report" / REPORT_NAME
    report_path.parent.mkdir(parents=True)
    report_path.write_text("stale\n", encoding="utf-8")
    report_path.chmod(0o644)

    code = trial_cli.main(
        ("register", *_register_arguments(tmp_path)),
        now=PREOPEN,
        runtime_code_version=CODE_VERSION,
    )

    assert code == 1
    assert not (tmp_path / "experiment.sqlite3").exists()
    assert not (tmp_path / "lane.sqlite3").exists()
    report = _report(tmp_path)
    assert "result: blocked_source" in report
    assert "external broker mutation: 0" in report
    assert report_path.stat().st_mode & 0o777 == 0o600
    _assert_redacted(report, tmp_path)


def test_register_and_start_create_then_replay_redacted_events(tmp_path: Path) -> None:
    _, experiments = _seed_lineage(tmp_path)

    registered = trial_cli.main(
        ("register", *_register_arguments(tmp_path)),
        now=PREOPEN,
        runtime_code_version=CODE_VERSION,
    )
    register_report = _report(tmp_path)
    register_replay = trial_cli.main(
        ("register", *_register_arguments(tmp_path)),
        now=OPEN,
        runtime_code_version=CODE_VERSION,
    )
    register_replay_report = _report(tmp_path)
    started = trial_cli.main(
        ("start", *_start_arguments(tmp_path)),
        now=OPEN + dt.timedelta(minutes=1),
    )
    start_report = _report(tmp_path)
    start_replay = trial_cli.main(
        ("start", *_start_arguments(tmp_path)),
        now=OPEN + dt.timedelta(minutes=2),
    )
    start_replay_report = _report(tmp_path)

    assert (registered, register_replay, started, start_replay) == (0, 0, 0, 0)
    assert "operation: register" in register_report
    assert "created: true" in register_report
    assert "created: false" in register_replay_report
    assert "operation: start" in start_report
    assert "event_kind: started" in start_report
    assert "created: true" in start_report
    assert "created: false" in start_replay_report
    assert len(experiments.trials()) == 1
    assert len(experiments.trial_events(experiments.trials()[0].registration.trial_id)) == 1
    assert (tmp_path / "report" / REPORT_NAME).stat().st_mode & 0o777 == 0o600
    for report in (register_report, register_replay_report, start_report, start_replay_report):
        _assert_redacted(report, tmp_path)


def test_finalize_reports_completed_and_exact_replay(tmp_path: Path) -> None:
    sources = _seed_terminal_sources(tmp_path)

    first = trial_cli.main(
        ("finalize", *_finalize_arguments(tmp_path, sources.session)),
        now=AFTER_CLOSE,
    )
    first_report = _report(tmp_path)
    replay = trial_cli.main(
        ("finalize", *_finalize_arguments(tmp_path, sources.session)),
        now=AFTER_CLOSE + dt.timedelta(minutes=5),
    )
    replay_report = _report(tmp_path)

    assert (first, replay) == (0, 0)
    assert "operation: finalize" in first_report
    assert "event_kind: completed" in first_report
    assert "created: true" in first_report
    assert "created: false" in replay_report
    assert len(sources.experiments.trial_events(sources.experiments.trials()[0].registration.trial_id)) == 2
    _assert_redacted(first_report, tmp_path)
    _assert_redacted(replay_report, tmp_path)


def test_finalize_reports_censored_without_raw_reasons(tmp_path: Path) -> None:
    sources = _seed_terminal_sources(tmp_path, censored=True)

    code = trial_cli.main(
        ("finalize", *_finalize_arguments(tmp_path, sources.session)),
        now=AFTER_CLOSE,
    )

    assert code == 0
    report = _report(tmp_path)
    assert "event_kind: censored" in report
    assert "fixture_daily_incident" not in report
    assert "forward_day_ineligible" not in report
    _assert_redacted(report, tmp_path)


def test_fail_reports_only_fixed_terminal_kind(tmp_path: Path) -> None:
    _, experiments = _seed_started_trial(tmp_path)
    audit = tmp_path / "post_session_metrics_cycles.csv"
    append_cycle_audit(audit, AFTER_CLOSE - dt.timedelta(minutes=10), 1)

    code = trial_cli.main(
        (
            "fail",
            *_fail_arguments(tmp_path, audit),
        ),
        now=AFTER_CLOSE,
    )

    assert code == 0
    report = _report(tmp_path)
    assert "operation: fail" in report
    assert "event_kind: failed" in report
    assert "paper_metrics_phase_failed" not in report
    assert len(experiments.trial_events(experiments.trials()[0].registration.trial_id)) == 2
    _assert_redacted(report, tmp_path)


def _register_arguments(tmp_path: Path) -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--lane-registry",
        str(tmp_path / "lane.sqlite3"),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--output-dir",
        str(tmp_path / "report"),
    )


def _start_arguments(tmp_path: Path) -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--output-dir",
        str(tmp_path / "report"),
    )


def _finalize_arguments(tmp_path: Path, session: Path) -> tuple[str, ...]:
    return (
        str(session),
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--lane-registry",
        str(tmp_path / "lane.sqlite3"),
        "--review-ledger",
        str(tmp_path / "review.sqlite3"),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--output-dir",
        str(tmp_path / "report"),
    )


def _fail_arguments(tmp_path: Path, audit: Path) -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--phase",
        OrbTrialFailurePhase.PAPER_METRICS.value,
        "--audit",
        str(audit),
        "--output-dir",
        str(tmp_path / "report"),
    )


def _report(tmp_path: Path) -> str:
    return (tmp_path / "report" / REPORT_NAME).read_text(encoding="utf-8")


def _assert_redacted(report: str, tmp_path: Path) -> None:
    forbidden = (
        str(tmp_path),
        ORB_CONTRACT.strategy_version,
        "orb-shadow-",
        "snapshot_key",
        "review_key",
        "account",
        "broker_order",
        "APCA_API",
        "https://",
    )
    assert all(value not in report for value in forbidden)


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV_DIRECTORY}:/usr/bin:/bin"
    return environment
