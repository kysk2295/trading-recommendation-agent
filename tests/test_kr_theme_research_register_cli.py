from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import run_kr_theme_research_register as registration_cli
from trading_agent.experiment_ledger_store import ExperimentLedgerStore

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "run_kr_theme_research_register.py"
EXAMPLE = PROJECT / "examples" / "kr_theme_projection" / "research-registration.json"
DAY_EXAMPLE = PROJECT / "examples" / "kr_theme_projection" / "day-research-registration.json"
REPORT_NAME = "kr_theme_research_registration_ko.md"


def test_kr_theme_research_register_direct_help_is_self_contained() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--manifest" in completed.stdout
    assert "Opportunity/day shadow" in completed.stdout


def test_kr_theme_research_register_fixture_replays_privately(tmp_path: Path) -> None:
    database = tmp_path / "experiment-ledger.sqlite3"
    output = tmp_path / "report"
    arguments = (
        "--manifest",
        str(EXAMPLE),
        "--database",
        str(database),
        "--output-dir",
        str(output),
    )

    assert registration_cli.main(arguments) == 0
    first_report = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert registration_cli.main(arguments) == 0
    replay_report = (output / REPORT_NAME).read_text(encoding="utf-8")

    assert "hypothesis 신규/재사용: 1/0" in first_report
    assert "strategy version 신규/재사용: 1/0" in first_report
    assert "hypothesis 신규/재사용: 0/1" in replay_report
    assert "strategy version 신규/재사용: 0/1" in replay_report
    assert "external mutation: 0" in replay_report
    assert len(ExperimentLedgerStore(database).multi_market_hypotheses()) == 1
    assert len(ExperimentLedgerStore(database).multi_market_strategy_versions()) == 1
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE((output / REPORT_NAME).stat().st_mode) == 0o600


def test_kr_theme_research_register_missing_manifest_fails_without_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing.sqlite3"
    output = tmp_path / "report"

    result = registration_cli.main(
        (
            "--manifest",
            str(tmp_path / "missing.json"),
            "--database",
            str(database),
            "--output-dir",
            str(output),
        )
    )

    assert result == 1
    assert not database.exists()
    assert "결과: blocked" in (output / REPORT_NAME).read_text(encoding="utf-8")


def test_kr_theme_day_research_register_reports_exact_lane(tmp_path: Path) -> None:
    database = tmp_path / "experiment-ledger.sqlite3"
    output = tmp_path / "report"

    result = registration_cli.main(
        (
            "--manifest",
            str(DAY_EXAMPLE),
            "--database",
            str(database),
            "--output-dir",
            str(output),
        )
    )

    report = (output / REPORT_NAME).read_text(encoding="utf-8")
    assert result == 0
    assert "lane: kr_equities/day_trading/theme_leader_vwap_reclaim" in report
    assert "operating mode: shadow" in report
