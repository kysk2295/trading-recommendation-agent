from __future__ import annotations

import datetime as dt
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Self

import httpx2
import pytest
import typer

import run_alpaca_sip_spot_capture as cli
from trading_agent.us_market_data_runtime_store import MarketDataRuntimeReader

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "run_alpaca_sip_spot_capture.py"
SOURCE_ID = "alpaca.sip.us_equities"
INSTRUMENT_ID = "us-eq-fixture-aapl"
SYMBOL = "AAPL"


def test_fixture_page_materializes_source_backed_spot_capture(
    tmp_path: Path,
) -> None:
    # Given
    fixture = tmp_path / "page.json"
    fixture.write_bytes(_fixture_page())
    state = tmp_path / "state"
    output = tmp_path / "output"

    # When
    completed = _run(fixture, state, output)

    # Then
    assert completed.returncode == 0, completed.stderr
    report = (output / "alpaca_sip_spot_capture_ko.md").read_text(encoding="utf-8")
    assert "- result: ready" in report
    assert "- latest completed bar: 2026-07-17T10:01:00-04:00" in report
    assert "- network access: 0" in report
    runtime = state / "runtime.sqlite3"
    assert MarketDataRuntimeReader(runtime).receipt_count(SOURCE_ID) == 31
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o600
    parquet = tuple((state / "canonical").rglob("events.parquet"))
    assert len(parquet) == 1
    assert stat.S_IMODE(parquet[0].stat().st_mode) == 0o600


def test_exact_spot_capture_replay_uses_local_evidence(
    tmp_path: Path,
) -> None:
    # Given
    fixture = tmp_path / "page.json"
    fixture.write_bytes(_fixture_page())
    state = tmp_path / "state"
    output = tmp_path / "output"
    assert _run(fixture, state, output).returncode == 0

    # When
    replay = _run(tmp_path / "missing-page.json", state, output)

    # Then
    assert replay.returncode == 0, replay.stderr
    assert "new_receipts=0" in replay.stdout
    report = (output / "alpaca_sip_spot_capture_ko.md").read_text(encoding="utf-8")
    assert "- new runtime receipts: 0" in report
    assert "- network access: 0" in report
    assert MarketDataRuntimeReader(state / "runtime.sqlite3").receipt_count(SOURCE_ID) == 31


def test_provider_forbidden_persists_private_source_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    class ForbiddenClient:
        base_url = httpx2.URL("https://data.alpaca.markets")
        follow_redirects = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            _exception_type: object,
            _exception: object,
            _traceback: object,
        ) -> None:
            pass

        def get(
            self,
            path: str,
            *,
            params: dict[str, str],
            headers: dict[str, str],
        ) -> httpx2.Response:
            assert headers == {
                "APCA-API-KEY-ID": "fixture-key",
                "APCA-API-SECRET-KEY": "fixture-secret",
            }
            request = httpx2.Request(
                "GET",
                f"https://data.alpaca.markets{path}",
                params=params,
            )
            return httpx2.Response(
                403,
                json={"message": "private provider detail"},
                request=request,
            )

    credentials = tmp_path / "alpaca.env"
    credentials.write_text(
        "APCA_API_KEY_ID=fixture-key\nAPCA_API_SECRET_KEY=fixture-secret\n",
        encoding="utf-8",
    )
    credentials.chmod(0o600)
    output = tmp_path / "output"
    monkeypatch.setattr(cli.httpx2, "Client", ForbiddenClient)
    monkeypatch.setattr(cli, "_require_current_live_as_of", lambda _value: None)

    # When
    with pytest.raises(typer.Exit) as raised:
        cli.main(
            instrument_id=INSTRUMENT_ID,
            symbol=SYMBOL,
            as_of="2026-07-23T12:01:30-04:00",
            state_dir=tmp_path / "state",
            output_dir=output,
            fixture_page=None,
            credentials_path=credentials,
        )

    # Then
    assert raised.value.exit_code == 2
    report_path = output / cli.REPORT_NAME
    report = report_path.read_text(encoding="utf-8")
    assert "- result: blocked_source" in report
    assert "- reason: sip_access_forbidden" in report
    assert "- provider status: 403" in report
    assert "private provider detail" not in report
    assert "fixture-secret" not in report
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def _run(
    fixture: Path,
    state: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--instrument-id",
            INSTRUMENT_ID,
            "--symbol",
            SYMBOL,
            "--as-of",
            "2026-07-17T10:01:30-04:00",
            "--state-dir",
            str(state),
            "--output-dir",
            str(output),
            "--fixture-page",
            str(fixture),
            "--credentials-path",
            str(ROOT / "missing-alpaca.env"),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _fixture_page() -> bytes:
    opened_at = dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC)
    bars = []
    for index in range(31):
        close = 100 + index / 10
        bars.append(
            {
                "t": (opened_at + dt.timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "o": close,
                "h": close + 0.5,
                "l": close - 0.5,
                "c": close,
                "v": 100 + index,
                "n": 10 + index,
                "vw": close,
            }
        )
    return json.dumps(
        {
            "bars": {SYMBOL: bars},
            "next_page_token": None,
        },
        separators=(",", ":"),
    ).encode()
