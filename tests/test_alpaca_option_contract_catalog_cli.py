from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import httpx2

import run_alpaca_option_contract_catalog as cli
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_option_chain_models import OptionContractType

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_alpaca_option_contract_catalog.py"


def test_fixture_cli_accepts_documented_provider_multiplier(
    tmp_path: Path,
) -> None:
    # Given an exact Alpaca contract with documented size and multiplier.
    raw_payload = json.dumps(
        {
            "option_contracts": [
                {
                    "id": "6e58f870-fe73-4583-81e4-b9a37892c36f",
                    "symbol": "AAPL260724C00200000",
                    "name": "AAPL Jul 24 2026 200 Call",
                    "status": "active",
                    "tradable": True,
                    "expiration_date": "2026-07-24",
                    "root_symbol": "AAPL",
                    "underlying_symbol": "AAPL",
                    "underlying_asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
                    "type": "call",
                    "style": "american",
                    "strike_price": "200",
                    "size": "100",
                    "multiplier": "100",
                    "open_interest": "1234",
                    "open_interest_date": "2026-07-22",
                    "close_price": "5.10",
                    "close_price_date": "2026-07-22",
                }
            ],
            "page_token": None,
            "limit": 100,
        },
        separators=(",", ":"),
    ).encode()
    fixture = tmp_path / "page.json"
    fixture.write_bytes(raw_payload)
    database = tmp_path / "catalog" / "option-contracts.sqlite3"
    output = tmp_path / "report"

    # When the public fixture CLI collects and projects that page.
    completed = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--collection-id",
            "m6-contract-fixture",
            "--underlying-symbol",
            "AAPL",
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

    # Then raw bytes and an option security-master terminal are private.
    assert completed.returncode == 0, completed.stderr
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT raw_payload FROM alpaca_option_contract_receipts"
        ).fetchone()
        terminal = connection.execute(
            "SELECT run_payload FROM alpaca_option_contract_runs"
        ).fetchone()
    assert stored == (raw_payload,)
    assert terminal is not None
    run = json.loads(terminal[0])
    assert run["contracts"][0]["instrument"]["market_domain"] == "us_derivatives"
    assert run["contracts"][0]["instrument"]["asset_class"] == "option"
    assert run["contracts"][0]["underlying_instrument_id"] == (
        "alpaca:b0b6dd9d-8b9b-48a9-ba46-b9d54906e415"
    )
    assert run["contracts"][0]["multiplier"] == "100"
    assert run["contracts"][0]["open_interest"] == 1234
    report = (
        output / "alpaca_option_contract_catalog_ko.md"
    ).read_text(encoding="utf-8")
    assert "- result: success" in report
    assert "- option contracts: 1" in report
    assert "- network access: 0" in report
    assert "- broker, account, or order mutation: none" in report
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(
        (output / "alpaca_option_contract_catalog_ko.md").stat().st_mode
    ) == 0o600


def test_live_cli_uses_exact_get_and_raw_first_terminal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Given a canonical Paper GET client and private credentials.
    raw_payload = json.dumps(
        {
            "option_contracts": [
                {
                    "id": "6e58f870-fe73-4583-81e4-b9a37892c36f",
                    "symbol": "AAPL260724C00200000",
                    "name": "AAPL Jul 24 2026 200 Call",
                    "status": "active",
                    "tradable": True,
                    "expiration_date": "2026-07-24",
                    "root_symbol": "AAPL",
                    "underlying_symbol": "AAPL",
                    "underlying_asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
                    "type": "call",
                    "style": "american",
                    "strike_price": "200",
                    "size": "100",
                    "multiplier": "100",
                    "open_interest": "1234",
                    "open_interest_date": "2026-07-22",
                    "close_price": "5.10",
                    "close_price_date": "2026-07-22",
                }
            ],
            "page_token": None,
            "limit": 100,
        },
        separators=(",", ":"),
    ).encode()
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            stream=httpx2.ByteStream(raw_payload),
        )

    def client() -> httpx2.Client:
        return httpx2.Client(
            base_url="https://paper-api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
            follow_redirects=False,
        )

    monkeypatch.setattr(
        cli,
        "load_private_alpaca_credentials",
        lambda path: AlpacaCredentials("fixture-key", "fixture-secret"),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "create_alpaca_option_contract_http_client",
        client,
        raising=False,
    )

    # When live mode runs without a fixture page.
    cli.main(
        collection_id="m6-contract-live",
        underlying_symbol="AAPL",
        expiration_date="2026-07-24",
        contract_type=OptionContractType.CALL,
        database=tmp_path / "catalog" / "option-contracts.sqlite3",
        output_dir=tmp_path / "report",
        limit=100,
        max_pages=2,
        fixture_page=None,
    )

    # Then one exact bounded GET supplies the raw-first terminal.
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v2/options/contracts"
    assert dict(requests[0].url.params) == {
        "expiration_date": "2026-07-24",
        "limit": "100",
        "status": "active",
        "type": "call",
        "underlying_symbols": "AAPL",
    }
    assert requests[0].headers["accept-encoding"] == "identity"
    assert requests[0].headers["APCA-API-KEY-ID"] == "fixture-key"
