from __future__ import annotations

import datetime as dt
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

import run_kr_theme_day_trial as trial_cli
from tests.test_kr_theme_day_trial import OPPORTUNITY_VERSION, _calendar_evidence, _register_authority
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kis_kr_session_calendar import project_kis_kr_session_calendar
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_trial.py"
VERSION = "kr-theme-leader-vwap-reclaim-v1-code-3a5b6542ec6b373b"
CODE = "kr-theme-day-fixture-code-v1"
REPORT = "kr_theme_day_trial_ko.md"


def _register_args(database: Path, output: Path, calendar_store: Path) -> tuple[str, ...]:
    return (
        "register",
        "--strategy-version",
        VERSION,
        "--code-version",
        CODE,
        "--opportunity-strategy-version",
        OPPORTUNITY_VERSION,
        "--session-date",
        "2026-07-20",
        "--registered-at",
        "2026-07-19T08:31:00+09:00",
        "--calendar-store",
        str(calendar_store),
        "--database",
        str(database),
        "--output-dir",
        str(output),
    )


def test_kr_theme_day_trial_help_is_local_shadow_only() -> None:
    completed = subprocess.run((str(SCRIPT), "--help"), cwd=ROOT, check=False, capture_output=True, text=True)
    register = subprocess.run(
        (str(SCRIPT), "register", "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "register" in completed.stdout
    assert "start" in completed.stdout
    assert "shadow" in completed.stdout
    assert register.returncode == 0
    assert "--calendar-store" in register.stdout


def test_kr_theme_day_trial_cli_registers_and_starts(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    output = tmp_path / "report"
    calendar_store = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    receipt, snapshot = _calendar_evidence()
    assert calendar_store.append(receipt, snapshot) is True
    _register_authority(ExperimentLedgerStore(database))

    assert (
        trial_cli.main(
            _register_args(database, output, calendar_store.path),
            clock=lambda: dt.datetime(2026, 7, 19, 8, 31, tzinfo=dt.timezone(dt.timedelta(hours=9))),
        )
        == 0
    )
    registration_report = (output / REPORT).read_text(encoding="utf-8")
    trial_id = ExperimentLedgerStore(database).multi_market_trials()[0].registration.trial_id
    assert (
        trial_cli.main(
            (
                "start",
                "--trial-id",
                trial_id,
                "--occurred-at",
                "2026-07-20T09:00:00+09:00",
                "--database",
                str(database),
                "--output-dir",
                str(output),
            ),
            clock=lambda: dt.datetime(2026, 7, 20, 9, 4, tzinfo=dt.timezone(dt.timedelta(hours=9))),
        )
        == 0
    )
    started_report = (output / REPORT).read_text(encoding="utf-8")

    assert "trial 신규/재사용: 1/0" in registration_report
    assert f"calendar snapshot: {snapshot.snapshot_id}" in registration_report
    assert "event 신규/재사용: 1/0" in started_report
    assert "order authority: false" in started_report
    assert stat.S_IMODE((output / REPORT).stat().st_mode) == 0o600


def test_kr_theme_day_trial_cli_preserves_causality_for_second_precision_registration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "experiment.sqlite3"
    output = tmp_path / "report"
    calendar_store = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    receipt, _ = _calendar_evidence()
    observed_at = dt.datetime(2026, 7, 19, 8, 31, 0, 259228, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    precise_receipt = replace(receipt, received_at=observed_at)
    assert calendar_store.append(precise_receipt, project_kis_kr_session_calendar(precise_receipt)) is True
    _register_authority(ExperimentLedgerStore(database))

    result = trial_cli.main(
        _register_args(database, output, calendar_store.path),
        clock=lambda: observed_at + dt.timedelta(milliseconds=100),
    )

    assert result == 0
    trial = ExperimentLedgerStore(database).multi_market_trials()[0].registration
    assert trial.registered_at == observed_at


def test_kr_theme_day_trial_cli_blocks_backdated_first_registration(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    output = tmp_path / "report"
    calendar_store = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    receipt, snapshot = _calendar_evidence()
    assert calendar_store.append(receipt, snapshot) is True
    _register_authority(ExperimentLedgerStore(database))

    result = trial_cli.main(
        _register_args(database, output, calendar_store.path),
        clock=lambda: dt.datetime(2026, 7, 19, 9, 1, tzinfo=dt.timezone(dt.timedelta(hours=9))),
    )

    assert result == 1
    assert ExperimentLedgerStore(database).multi_market_trials() == ()


def test_kr_theme_day_trial_cli_blocks_future_first_start(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    output = tmp_path / "report"
    calendar_store = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    receipt, snapshot = _calendar_evidence()
    assert calendar_store.append(receipt, snapshot) is True
    _register_authority(ExperimentLedgerStore(database))
    assert (
        trial_cli.main(
            _register_args(database, output, calendar_store.path),
            clock=lambda: dt.datetime(2026, 7, 19, 8, 31, tzinfo=dt.timezone(dt.timedelta(hours=9))),
        )
        == 0
    )
    ledger = ExperimentLedgerStore(database)
    trial_id = ledger.multi_market_trials()[0].registration.trial_id

    result = trial_cli.main(
        (
            "start",
            "--trial-id",
            trial_id,
            "--occurred-at",
            "2026-07-20T09:00:00+09:00",
            "--database",
            str(database),
            "--output-dir",
            str(output),
        ),
        clock=lambda: dt.datetime(2026, 7, 20, 8, 59, 45, tzinfo=dt.timezone(dt.timedelta(hours=9))),
    )

    assert result == 1
    assert ledger.multi_market_trial_events(trial_id) == ()


def test_kr_theme_day_trial_cli_blocks_missing_calendar_store(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    output = tmp_path / "report"
    _register_authority(ExperimentLedgerStore(database))

    result = trial_cli.main(_register_args(database, output, tmp_path / "missing.sqlite3"))

    assert result == 1
    assert ExperimentLedgerStore(database).multi_market_trials() == ()
    assert "결과: blocked" in (output / REPORT).read_text(encoding="utf-8")
