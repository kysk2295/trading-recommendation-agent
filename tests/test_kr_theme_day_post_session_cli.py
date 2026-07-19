from __future__ import annotations

import csv
import datetime as dt
import stat
import subprocess
from pathlib import Path

import run_kr_theme_day_lifecycle as lifecycle_cli
import run_kr_theme_day_post_session as post_session_cli
import run_kr_theme_day_reviewer as reviewer_cli
import run_kr_theme_day_trial_terminal as terminal_cli
from tests.test_kr_theme_day_lifecycle import DECIDED_AT, _calendar_evidence
from tests.test_kr_theme_day_reviewer import REVIEWED_AT
from tests.test_kr_theme_day_shadow_entry import VERSION, _ledger
from tests.test_kr_theme_day_trial_terminal import CLOSED_AT, _trial_stores
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_day_review_store import KrThemeDayReviewStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_post_session.py"
SESSION = dt.date(2026, 7, 20)
STARTED_AT = dt.datetime(2026, 7, 20, 15, 31, tzinfo=dt.UTC)


def _paths(tmp_path: Path) -> post_session_cli.KrThemeDayPostSessionPaths:
    return post_session_cli.KrThemeDayPostSessionPaths(
        experiment_ledger=tmp_path / "experiment.sqlite3",
        entry_store=tmp_path / "entries.sqlite3",
        exit_store=tmp_path / "exits.sqlite3",
        terminal_store=tmp_path / "terminals.sqlite3",
        review_store=tmp_path / "reviews.sqlite3",
        calendar_store=tmp_path / "calendar.sqlite3",
        output_dir=tmp_path / "post-session",
    )


def _request(tmp_path: Path, trial_id: str = "trial-fixture") -> post_session_cli.KrThemeDayPostSessionRequest:
    return post_session_cli.KrThemeDayPostSessionRequest(
        paths=_paths(tmp_path),
        trial_id=trial_id,
        strategy_version=VERSION,
        session_date=SESSION,
    )


def _args(request: post_session_cli.KrThemeDayPostSessionRequest) -> tuple[str, ...]:
    paths = request.paths
    return (
        "--experiment-ledger",
        str(paths.experiment_ledger),
        "--entry-store",
        str(paths.entry_store),
        "--exit-store",
        str(paths.exit_store),
        "--terminal-store",
        str(paths.terminal_store),
        "--review-store",
        str(paths.review_store),
        "--calendar-store",
        str(paths.calendar_store),
        "--trial-id",
        request.trial_id,
        "--strategy-version",
        request.strategy_version,
        "--session-date",
        request.session_date.isoformat(),
        "--output-dir",
        str(paths.output_dir),
    )


def _audit_rows(path: Path) -> list[tuple[str, str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["started_at", "exit_code", "status"]
    return [(row[0], row[1], row[2]) for row in rows[1:]]


def test_help_and_invalid_date_expose_only_local_control_inputs() -> None:
    help_result = subprocess.run((str(SCRIPT), "--help"), cwd=ROOT, check=False, capture_output=True, text=True)
    invalid = subprocess.run(
        (
            str(SCRIPT),
            "--experiment-ledger",
            "missing",
            "--entry-store",
            "missing",
            "--exit-store",
            "missing",
            "--terminal-store",
            "missing",
            "--review-store",
            "missing",
            "--calendar-store",
            "missing",
            "--trial-id",
            "missing",
            "--strategy-version",
            "missing",
            "--session-date",
            "invalid",
            "--output-dir",
            "missing",
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert help_result.returncode == 0
    assert invalid.returncode == 2
    for forbidden in ("--account", "--arm", "--credential", "--endpoint", "--force", "--order"):
        assert forbidden not in help_result.stdout.lower()


def test_child_commands_are_exact_and_have_no_time_or_authority_override(tmp_path: Path) -> None:
    request = _request(tmp_path)
    commands = (
        post_session_cli.terminal_command(request),
        post_session_cli.reviewer_command(request),
        post_session_cli.lifecycle_command(request),
    )

    assert Path(commands[0][0]).name == "run_kr_theme_day_trial_terminal.py"
    assert Path(commands[1][0]).name == "run_kr_theme_day_reviewer.py"
    assert Path(commands[2][0]).name == "run_kr_theme_day_lifecycle.py"
    joined = " ".join(value for command in commands for value in command).lower()
    for forbidden in (
        "--account",
        "--arm",
        "--credential",
        "--decided-at",
        "--endpoint",
        "--force",
        "--occurred-at",
        "--order",
        "--reviewed-at",
    ):
        assert forbidden not in joined


def test_phase_failure_stops_every_later_child_and_audits_attempt(tmp_path: Path) -> None:
    request = _request(tmp_path)
    calls: list[tuple[str, ...]] = []

    def fail_terminal(command: tuple[str, ...]) -> int:
        calls.append(command)
        return 1

    result = post_session_cli.run_post_session(
        request,
        runner=fail_terminal,
        clock=lambda: STARTED_AT,
    )

    assert result.terminal_exit_code == 1
    assert result.reviewer_exit_code is None
    assert result.lifecycle_exit_code is None
    assert calls == [post_session_cli.terminal_command(request)]
    assert _audit_rows(request.paths.output_dir / post_session_cli.TERMINAL_AUDIT_NAME) == [
        (STARTED_AT.isoformat(), "1", "failed")
    ]
    assert not (request.paths.output_dir / post_session_cli.REVIEWER_AUDIT_NAME).exists()


def test_reviewer_failure_stops_lifecycle_child(tmp_path: Path) -> None:
    request = _request(tmp_path)
    calls: list[tuple[str, ...]] = []
    exits = iter((0, 1))

    result = post_session_cli.run_post_session(
        request,
        runner=lambda command: calls.append(command) or next(exits),
        clock=lambda: STARTED_AT,
    )

    assert (result.terminal_exit_code, result.reviewer_exit_code, result.lifecycle_exit_code) == (0, 1, None)
    assert calls == [
        post_session_cli.terminal_command(request),
        post_session_cli.reviewer_command(request),
    ]
    assert not (request.paths.output_dir / post_session_cli.LIFECYCLE_AUDIT_NAME).exists()


def test_fixture_happy_path_and_replay_run_all_real_child_mains(tmp_path: Path) -> None:
    _, trial_id = _trial_stores(tmp_path)
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    calendar = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    receipt, snapshot = _calendar_evidence()
    assert calendar.append(receipt, snapshot) is True
    request = _request(tmp_path, trial_id)
    replay_offset = dt.timedelta(minutes=1)
    offset = dt.timedelta()

    def run(command: tuple[str, ...]) -> int:
        args = command[1:]
        name = Path(command[0]).name
        if name == "run_kr_theme_day_trial_terminal.py":
            return terminal_cli.main(args, occurred_at=CLOSED_AT + offset)
        if name == "run_kr_theme_day_reviewer.py":
            return reviewer_cli.main(args, reviewed_at=REVIEWED_AT + offset)
        if name == "run_kr_theme_day_lifecycle.py":
            return lifecycle_cli.main(args, decided_at=DECIDED_AT + offset)
        raise AssertionError(name)

    first = post_session_cli.main(_args(request), runner=run, clock=lambda: STARTED_AT)
    offset = replay_offset
    replay = post_session_cli.main(
        _args(request),
        runner=run,
        clock=lambda: STARTED_AT + replay_offset,
    )

    assert (first, replay) == (0, 0)
    assert len(ledger.multi_market_trial_events(trial_id)) == 2
    assert len(KrThemeDayReviewStore(tmp_path / "reviews.sqlite3").events()) == 1
    assert len(ExperimentLedgerStore(tmp_path / "experiment.sqlite3").multi_market_lifecycle_events(VERSION)) == 1
    for audit_name in (
        post_session_cli.TERMINAL_AUDIT_NAME,
        post_session_cli.REVIEWER_AUDIT_NAME,
        post_session_cli.LIFECYCLE_AUDIT_NAME,
    ):
        audit = request.paths.output_dir / audit_name
        assert len(_audit_rows(audit)) == 2
        assert stat.S_IMODE(audit.stat().st_mode) == 0o600
    report = (request.paths.output_dir / post_session_cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "result: completed_control_cycle" in report
    assert "terminal phase: success" in report
    assert "Reviewer phase: success" in report
    assert "lifecycle phase: success" in report
    assert "external account/order mutation: 0" in report
    assert trial_id not in report
    assert VERSION not in report
    assert stat.S_IMODE((request.paths.output_dir / post_session_cli.REPORT_NAME).stat().st_mode) == 0o600
