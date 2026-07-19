from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

import run_kr_theme_day_intraday as intraday_cli
from tests.test_kis_kr_market_projection import _opportunity
from tests.test_kr_theme_day_intraday import _receipt_store
from tests.test_kr_theme_day_shadow_entry import VERSION, _ledger
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kr_theme_day_intraday.py"


def test_intraday_cli_help_exposes_local_read_only_projection() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "receipt-store" in completed.stdout
    assert "entry-store" in completed.stdout
    assert "credential" not in completed.stdout
    assert "order" not in completed.stdout


def test_intraday_cli_projects_fixture_receipts_and_replays(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    _ = _ledger(database)
    receipt_store = _receipt_store(tmp_path)
    entry_store = tmp_path / "entries.sqlite3"
    outbox = tmp_path / "opportunities.jsonl"
    assert append_opportunity_snapshot(outbox, _opportunity()) is True
    outbox.chmod(0o600)
    output = tmp_path / "report"
    args = _args(database, receipt_store.path, entry_store, outbox, output)

    first = intraday_cli.main(args)
    second = intraday_cli.main(args)

    report = (output / "kr_theme_day_intraday_ko.md").read_text(encoding="utf-8")
    assert first == 0
    assert second == 0
    assert len(KrThemeDayShadowEntryStore(entry_store).entries()) == 1
    assert "결과: entry_replayed" in report
    assert "order authority: false" in report
    assert "external mutation: 0" in report
    assert "005930" not in report
    assert stat.S_IMODE((output / "kr_theme_day_intraday_ko.md").stat().st_mode) == 0o600


def test_intraday_cli_blocks_missing_opportunity_without_entry(tmp_path: Path) -> None:
    database = tmp_path / "experiment.sqlite3"
    _ = _ledger(database)
    receipt_store = _receipt_store(tmp_path)
    entry_store = tmp_path / "entries.sqlite3"

    result = intraday_cli.main(
        _args(database, receipt_store.path, entry_store, tmp_path / "missing.jsonl", tmp_path / "report")
    )

    assert result == 1
    assert not entry_store.exists()


def test_intraday_cli_blocks_opportunity_changed_after_onboarding(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "experiment.sqlite3"
    _ = _ledger(database)
    receipt_store = _receipt_store(tmp_path)
    entry_store = tmp_path / "entries.sqlite3"
    outbox = tmp_path / "opportunities.jsonl"
    assert append_opportunity_snapshot(outbox, _opportunity()) is True
    outbox.chmod(0o600)
    args = list(_args(database, receipt_store.path, entry_store, outbox, tmp_path / "report"))
    args[args.index("--opportunity-sha256") + 1] = "0" * 64

    # When
    result = intraday_cli.main(tuple(args))

    # Then
    assert result == 1
    assert not entry_store.exists()


def _args(
    database: Path,
    receipt_store: Path,
    entry_store: Path,
    outbox: Path,
    output: Path,
) -> tuple[str, ...]:
    return (
        "--opportunity-outbox",
        str(outbox),
        "--opportunity-id",
        "KR-THEME-OPPORTUNITY-001",
        "--opportunity-sha256",
        _opportunity_sha256(),
        "--strategy-version",
        VERSION,
        "--evaluated-at",
        "2026-07-20T09:04:04+09:00",
        "--filled-at",
        "2026-07-20T09:04:05+09:00",
        "--database",
        str(database),
        "--receipt-store",
        str(receipt_store),
        "--entry-store",
        str(entry_store),
        "--output-dir",
        str(output),
    )


def _opportunity_sha256() -> str:
    payload = json.dumps(
        _opportunity().model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
