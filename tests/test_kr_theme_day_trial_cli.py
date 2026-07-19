from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import run_kr_theme_day_trial as trial_cli
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.kr_theme_research_registration import register_kr_theme_research_manifest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_trial.py"
MANIFEST = ROOT / "examples" / "kr_theme_projection" / "day-research-registration.json"
VERSION = "kr-theme-leader-vwap-reclaim-v1-code-3a5b6542ec6b373b"
CODE = "kr-theme-day-fixture-code-v1"
REPORT = "kr_theme_day_trial_ko.md"


def _register_args(database: Path, output: Path) -> tuple[str, ...]:
    return (
        "register",
        "--strategy-version",
        VERSION,
        "--code-version",
        CODE,
        "--session-date",
        "2026-07-20",
        "--registered-at",
        "2026-07-19T08:31:00+09:00",
        "--database",
        str(database),
        "--output-dir",
        str(output),
    )


def test_kr_theme_day_trial_help_is_local_shadow_only() -> None:
    completed = subprocess.run((str(SCRIPT), "--help"), cwd=ROOT, check=False, capture_output=True, text=True)

    assert completed.returncode == 0
    assert "register" in completed.stdout
    assert "start" in completed.stdout
    assert "shadow" in completed.stdout


def test_kr_theme_day_trial_cli_registers_and_starts(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    output = tmp_path / "report"
    _ = register_kr_theme_research_manifest(MANIFEST, ExperimentLedgerStore(database))

    assert trial_cli.main(_register_args(database, output)) == 0
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
            )
        )
        == 0
    )
    started_report = (output / REPORT).read_text(encoding="utf-8")

    assert "trial 신규/재사용: 1/0" in registration_report
    assert "event 신규/재사용: 1/0" in started_report
    assert "order authority: false" in started_report
    assert stat.S_IMODE((output / REPORT).stat().st_mode) == 0o600
