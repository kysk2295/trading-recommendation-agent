from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
from pathlib import Path
from typing import Never

import pytest

import run_kis_kr_market_collect as collect_cli
from tests.test_kis_kr_market_projection import (
    _minute_body,
    _price_body,
    _quote_body,
)
from tests.test_kr_theme_day_trial import _calendar_evidence
from trading_agent.kis_auth import KisMode
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_kis_kr_market_collect.py"


def test_collect_cli_help_is_get_only() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "fixture-manifest" in completed.stdout
    assert "receipt-store" in completed.stdout
    assert "order" not in completed.stdout
    assert "account" not in completed.stdout


def test_collect_cli_fixture_appends_three_receipts_and_replays(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    calendar_store, snapshot_id = _calendar_store(tmp_path)
    receipt_store = tmp_path / "receipts.sqlite3"
    output = tmp_path / "report"
    args = _args(fixture, calendar_store, snapshot_id, receipt_store, output)

    first = collect_cli.main(args)
    second = collect_cli.main(args)

    report = (output / "kis_kr_market_collection_ko.md").read_text(encoding="utf-8")
    assert first == 0
    assert second == 0
    assert len(KisKrMarketReceiptStore(receipt_store).receipts()) == 3
    assert "receipt 신규/재사용: 0/3" in report
    assert "provider mode: fixture" in report
    assert "external mutation: 0" in report
    assert "005930" not in report
    assert stat.S_IMODE((output / "kis_kr_market_collection_ko.md").stat().st_mode) == 0o600


def test_collect_cli_blocks_wrong_calendar_before_receipt_store(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    calendar_store, _ = _calendar_store(tmp_path)
    receipt_store = tmp_path / "receipts.sqlite3"

    result = collect_cli.main(_args(fixture, calendar_store, "0" * 64, receipt_store, tmp_path / "blocked"))

    assert result == 1
    assert not receipt_store.exists()


def test_collect_cli_blocks_closed_session_before_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar_store, snapshot_id = _calendar_store(tmp_path)
    receipt_store = tmp_path / "receipts.sqlite3"

    def forbidden_credentials(_: KisMode) -> Never:
        raise AssertionError("credential loader must not run")

    monkeypatch.setattr(
        collect_cli,
        "_current_time",
        lambda: dt.datetime(2026, 7, 19, 10, tzinfo=dt.timezone(dt.timedelta(hours=9))),
    )
    monkeypatch.setattr(collect_cli, "load_kis_credentials", forbidden_credentials)

    result = collect_cli.main(
        (
            "--symbol",
            "005930",
            "--calendar-store",
            str(calendar_store),
            "--calendar-snapshot-id",
            snapshot_id,
            "--receipt-store",
            str(receipt_store),
            "--output-dir",
            str(tmp_path / "blocked"),
        )
    )

    assert result == 1
    assert not receipt_store.exists()


def _fixture(tmp_path: Path) -> Path:
    payloads = {
        "minute.json": _minute_body(),
        "price.json": _price_body(),
        "quote.json": _quote_body(),
    }
    for name, payload in payloads.items():
        (tmp_path / name).write_bytes(payload)
    manifest = tmp_path / "fixture.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "symbol": "005930",
                "requested_at": "2026-07-20T09:04:01+09:00",
                "receipts": [
                    {
                        "kind": "minute_bars",
                        "received_at": "2026-07-20T09:04:02+09:00",
                        "payload_path": "minute.json",
                    },
                    {
                        "kind": "price_status",
                        "received_at": "2026-07-20T09:04:02+09:00",
                        "payload_path": "price.json",
                    },
                    {
                        "kind": "order_book",
                        "received_at": "2026-07-20T09:04:03+09:00",
                        "payload_path": "quote.json",
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return manifest


def _calendar_store(tmp_path: Path) -> tuple[Path, str]:
    store = KisKrSessionCalendarStore(tmp_path / "calendar.sqlite3")
    receipt, snapshot = _calendar_evidence()
    assert store.append(receipt, snapshot) is True
    return store.path, snapshot.snapshot_id


def _args(
    fixture: Path,
    calendar_store: Path,
    snapshot_id: str,
    receipt_store: Path,
    output: Path,
) -> tuple[str, ...]:
    return (
        "--symbol",
        "005930",
        "--calendar-store",
        str(calendar_store),
        "--calendar-snapshot-id",
        snapshot_id,
        "--receipt-store",
        str(receipt_store),
        "--output-dir",
        str(output),
        "--fixture-manifest",
        str(fixture),
    )
