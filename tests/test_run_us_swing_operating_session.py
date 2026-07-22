from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path

from run_us_swing_operating_session import main
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.swing_shadow_store import SwingShadowReader
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
SCRIPT = ROOT / "run_us_swing_operating_session.py"
SESSION = dt.date(2026, 7, 17)


def test_cli_fixture_tick_runs_scanner_registers_trial_and_replays(tmp_path: Path) -> None:
    # Given: isolated local ledgers and a completed-day scanner fixture.
    experiment = tmp_path / "experiment.sqlite3"
    shadow = tmp_path / "shadow.sqlite3"
    delivery = tmp_path / "delivery.sqlite3"
    reviews = tmp_path / "reviews.sqlite3"
    output = tmp_path / "output"
    fixture = _write_signal_fixture(tmp_path)
    arguments = (
        "--session-date",
        SESSION.isoformat(),
        "--fixture-root",
        str(fixture),
        "--research-manifest",
        str(MANIFEST),
        "--experiment-ledger",
        str(experiment),
        "--shadow-ledger",
        str(shadow),
        "--delivery-store",
        str(delivery),
        "--review-ledger",
        str(reviews),
        "--output-dir",
        str(output),
    )
    now = dt.datetime.combine(SESSION, dt.time(16, 5), tzinfo=NEW_YORK)

    # When: the same operating tick is invoked twice through the CLI boundary.
    first = main(arguments, now=now, runtime_code_version="test_code_v1")
    replay = main(arguments, now=now + dt.timedelta(minutes=1), runtime_code_version="test_code_v1")

    # Then: one WATCH and one prospective trial exist, with a private replay report.
    assert first == 0
    assert replay == 0
    assert len(SwingShadowReader(shadow).signals()) == 1
    assert len(ExperimentLedgerReader(experiment).trials()) == 1
    assert tuple(event.kind for event in HermesDeliveryStore(delivery).events()) == (
        HermesDeliveryKind.WATCH,
    )
    report = output / "us_swing_operating_session_ko.md"
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert "scanner_executed: false" in report.read_text(encoding="utf-8")
    assert "external broker mutations: 0" in report.read_text(encoding="utf-8")


def test_cli_help_exposes_one_tick_operating_contract() -> None:
    # Given: the operating entrypoint.
    command = (sys.executable, str(SCRIPT), "--help")

    # When: an operator requests help.
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    # Then: the command is discoverable without touching any ledger or broker.
    assert completed.returncode == 0
    assert "--session-date" in completed.stdout
    assert "--fixture-root" in completed.stdout
    assert "--universe-file" in completed.stdout
    assert "--auto-universe" in completed.stdout
    assert "--experiment-ledger" in completed.stdout


def _write_signal_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    observed_at = dt.datetime.combine(SESSION, dt.time(16, 5), tzinfo=NEW_YORK)
    sessions = _following_sessions(SESSION, 21)
    manifest = {
        "schema_version": 1,
        "session_date": SESSION.isoformat(),
        "observed_at": observed_at.isoformat(),
        "universe_id": "fixture_universe_v1",
        "symbols": ["ACME"],
        "bars_file": "daily-bars.json",
    }
    bars = [
        {
            "symbol": "ACME",
            "session_date": session_date.isoformat(),
            "open": "10",
            "high": "15.2" if index == len(sessions) - 1 else "10.2",
            "low": "9.9",
            "close": "15" if index == len(sessions) - 1 else "10",
            "volume": 200_000 if index == len(sessions) - 1 else 100_000,
        }
        for index, session_date in enumerate(sessions)
    ]
    (fixture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (fixture / "daily-bars.json").write_text(json.dumps(bars), encoding="utf-8")
    return fixture


def _following_sessions(session_date: dt.date, count: int) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = session_date
    for _ in range(100):
        if regular_session_bounds(current) is not None:
            sessions.append(current)
            if len(sessions) == count:
                return tuple(reversed(sessions))
        current -= dt.timedelta(days=1)
    raise AssertionError("fixture could not find enough regular sessions")
