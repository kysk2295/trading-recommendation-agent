from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import run_kr_theme_day_shadow_exit as exit_cli
from tests.test_kr_theme_day_intraday import _request
from tests.test_kr_theme_day_shadow_entry import _ledger
from tests.test_kr_theme_day_shadow_exit_cycle import _entry_receipts, _exit_receipt
from trading_agent.kr_theme_day_intraday import run_kr_theme_day_intraday_entry
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_shadow_exit_store import KrThemeDayShadowExitStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_shadow_exit.py"


def test_shadow_exit_cli_help_is_local_only() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "receipt-store" in completed.stdout
    assert "exit-store" in completed.stdout
    assert "order" not in completed.stdout
    assert "account" not in completed.stdout


def test_shadow_exit_cli_projects_and_replays_without_identifiers(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    receipt_store = _entry_receipts(tmp_path)
    entry_store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    _ = run_kr_theme_day_intraday_entry(ledger, receipt_store, entry_store, _request())
    assert receipt_store.append(_exit_receipt(high="107")) is True
    exit_store = tmp_path / "exits.sqlite3"
    output = tmp_path / "report"
    trial_id = ledger.multi_market_trials()[0].registration.trial_id
    args = (
        "--trial-id",
        trial_id,
        "--evaluated-at",
        "2026-07-20T09:06:03+09:00",
        "--receipt-store",
        str(receipt_store.path),
        "--entry-store",
        str(entry_store.path),
        "--exit-store",
        str(exit_store),
        "--output-dir",
        str(output),
    )

    first = exit_cli.main(args)
    second = exit_cli.main(args)

    report = (output / "kr_theme_day_shadow_exit_ko.md").read_text(encoding="utf-8")
    assert first == 0
    assert second == 0
    assert len(KrThemeDayShadowExitStore(exit_store).exits()) == 1
    assert "terminal/open/pending/new: 1/0/0/0" in report
    assert "order authority: false" in report
    assert trial_id not in report
    assert "005930" not in report
    assert stat.S_IMODE((output / "kr_theme_day_shadow_exit_ko.md").stat().st_mode) == 0o600
