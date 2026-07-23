from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_alpaca_option_chain_collect.py"


def test_fixture_cli_preserves_raw_option_chain_and_reports_success(
    tmp_path: Path,
) -> None:
    # Given a bounded indicative option-chain page from the provider contract.
    raw_payload = json.dumps(
        {
            "snapshots": {
                "AAPL260724C00200000": {
                    "latestQuote": {
                        "t": "2026-07-23T14:31:00Z",
                        "ax": "C",
                        "ap": 5.2,
                        "as": 3,
                        "bx": "P",
                        "bp": 5.0,
                        "bs": 4,
                        "c": "R",
                    },
                    "impliedVolatility": 0.31,
                    "greeks": {
                        "delta": 0.55,
                        "gamma": 0.04,
                        "rho": 0.02,
                        "theta": -0.08,
                        "vega": 0.11,
                    },
                }
            },
            "next_page_token": None,
        },
        separators=(",", ":"),
    ).encode()
    fixture = tmp_path / "page.json"
    fixture.write_bytes(raw_payload)
    database = tmp_path / "option-chain.sqlite3"
    output = tmp_path / "report"

    # When the real CLI collects the fixture through its public surface.
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--collection-id",
            "m6-aapl-fixture",
            "--underlying-symbol",
            "AAPL",
            "--feed",
            "indicative",
            "--expiration-date",
            "2026-07-24",
            "--contract-type",
            "call",
            "--limit",
            "100",
            "--max-pages",
            "2",
            "--database",
            str(database),
            "--output-dir",
            str(output),
            "--fixture-page",
            str(fixture),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then the exact wire bytes and a private success report are durable.
    assert completed.returncode == 0, completed.stderr
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT raw_payload FROM alpaca_option_chain_receipts"
        ).fetchone()
    assert stored == (raw_payload,)
    report = (output / "alpaca_option_chain_collection_ko.md").read_text(
        encoding="utf-8"
    )
    assert "- result: success" in report
    assert "- source feed: indicative" in report
    assert "- option snapshots: 1" in report
    assert "- network access: 0" in report
    assert "- broker, account, or order mutation: none" in report
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(
        (output / "alpaca_option_chain_collection_ko.md").stat().st_mode
    ) == 0o600
