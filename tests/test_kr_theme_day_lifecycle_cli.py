from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import run_kr_theme_day_lifecycle as lifecycle_cli
from tests.test_kr_theme_day_lifecycle import (
    DECIDED_AT,
    _calendar_evidence,
    _reviewed_sources,
)
from tests.test_kr_theme_day_shadow_entry import VERSION
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_lifecycle.py"
REPORT = "kr_theme_day_lifecycle_ko.md"


def _args(tmp_path: Path, calendar: Path, output: Path) -> tuple[str, ...]:
    return (
        "--experiment-ledger",
        str(tmp_path / "experiment.sqlite3"),
        "--entry-store",
        str(tmp_path / "entries.sqlite3"),
        "--exit-store",
        str(tmp_path / "exits.sqlite3"),
        "--terminal-store",
        str(tmp_path / "terminals.sqlite3"),
        "--review-store",
        str(tmp_path / "reviews.sqlite3"),
        "--calendar-store",
        str(calendar),
        "--strategy-version",
        VERSION,
        "--as-of-session",
        "2026-07-20",
        "--output-dir",
        str(output),
    )


def test_cli_help_exposes_only_local_evidence_paths() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--calendar-store" in completed.stdout
    assert "--review-store" in completed.stdout
    assert "account" not in completed.stdout.lower()
    assert "order" not in completed.stdout.lower()


def test_cli_registers_and_replays_sanitized_lifecycle_report(tmp_path: Path) -> None:
    ledger, _ = _reviewed_sources(tmp_path)
    calendar_store = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    receipt, snapshot = _calendar_evidence()
    assert calendar_store.append(receipt, snapshot) is True
    output = tmp_path / "report"
    args = _args(tmp_path, calendar_store.path, output)

    assert lifecycle_cli.main(args, decided_at=DECIDED_AT) == 0
    first = (output / REPORT).read_text(encoding="utf-8")
    assert lifecycle_cli.main(args, decided_at=DECIDED_AT) == 0
    replay = (output / REPORT).read_text(encoding="utf-8")

    assert "outcome: registered" in first
    assert "created: true" in first
    assert "created: false" in replay
    assert "automatic champion: false" in replay
    assert "external account/order mutation: 0" in replay
    assert VERSION not in replay
    assert snapshot.snapshot_id not in replay
    assert len(ledger.multi_market_lifecycle_events(VERSION)) == 1
    assert stat.S_IMODE((output / REPORT).stat().st_mode) == 0o600


def test_cli_blocks_missing_calendar_without_append(tmp_path: Path) -> None:
    ledger, _ = _reviewed_sources(tmp_path)
    output = tmp_path / "report"

    result = lifecycle_cli.main(
        _args(tmp_path, tmp_path / "missing-calendar.sqlite3", output),
        decided_at=DECIDED_AT,
    )

    assert result == 1
    assert ledger.multi_market_lifecycle_events(VERSION) == ()
    report = (output / REPORT).read_text(encoding="utf-8")
    assert "result: blocked_source" in report
    assert "external account/order mutation: 0" in report
