from __future__ import annotations

import datetime as dt
import json
import os
import stat
from pathlib import Path

import httpx2

import run_alpaca_security_master as cli
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_security_master_store import AlpacaSecurityMasterStore

OBSERVED_AT = dt.datetime(2026, 7, 19, 3, 0, tzinfo=dt.UTC)
BODY = json.dumps(
    (
        {
            "id": "asset-live",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": "FIXT",
            "name": "Fixture Inc",
            "status": "active",
            "tradable": True,
        },
    ),
    separators=(",", ":"),
).encode()


def test_cli_collects_private_raw_first_security_master(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def client() -> httpx2.Client:
        return httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, content=BODY)
            ),
            follow_redirects=False,
        )

    monkeypatch.setattr(
        cli,
        "load_alpaca_credentials",
        lambda path: AlpacaCredentials("fixture-key", "fixture-secret"),
    )
    monkeypatch.setattr(cli, "create_alpaca_security_master_client", client)
    store_path = tmp_path / "security-master.sqlite3"
    output = tmp_path / "report"

    code = cli.main(
        (
            "--store",
            str(store_path),
            "--output-dir",
            str(output),
        ),
        clock=lambda: OBSERVED_AT,
    )

    assert code == 0
    assert AlpacaSecurityMasterStore(store_path).snapshot_count() == 1
    report = output / "alpaca_security_master_ko.md"
    content = report.read_text(encoding="utf-8")
    assert "active instrument: 1" in content
    assert "network GET: 1" in content
    assert "account/order mutation: 0" in content
    assert "FIXT" not in content
    assert "fixture-key" not in content
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600


def test_cli_bad_arguments_exit_before_credentials(monkeypatch) -> None:
    loaded = False

    def load(path: Path) -> AlpacaCredentials:
        nonlocal loaded
        loaded = True
        return AlpacaCredentials("fixture-key", "fixture-secret")

    monkeypatch.setattr(cli, "load_alpaca_credentials", load)

    try:
        _ = cli.parse_args(("--store", "only.sqlite3"))
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("missing output-dir must fail")

    assert loaded is False
