from __future__ import annotations

import os
import stat
from pathlib import Path

import httpx2

import run_alpaca_sip_historical_profile as cli
from tests.test_alpaca_sip_historical_profile import _response
from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.us_intraday_volume_profile_artifact import (
    IntradayVolumeProfileArtifactStore,
)


def test_cli_persists_profile_and_replays_without_new_get(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[httpx2.Request] = []

    def client() -> httpx2.Client:
        def respond(request: httpx2.Request) -> httpx2.Response:
            calls.append(request)
            return _response(request, complete=True)

        return httpx2.Client(
            base_url=ALPACA_DATA_URL,
            transport=httpx2.MockTransport(respond),
            follow_redirects=False,
        )

    monkeypatch.setattr(cli, "create_data_client", client)
    monkeypatch.setattr(
        cli,
        "load_alpaca_credentials",
        lambda _path: AlpacaCredentials("fixture-key", "fixture-secret"),
    )
    state = tmp_path / "state"
    output = tmp_path / "report"
    args = (
        "--instrument-id",
        "alpaca:asset-acme",
        "--symbol",
        "ACME",
        "--target-session-date",
        "2026-07-17",
        "--through-minute",
        "35",
        "--state-dir",
        str(state),
        "--output-dir",
        str(output),
    )

    assert cli.main(args) == 0
    assert len(calls) == 20
    assert cli.main(args) == 0
    assert len(calls) == 20

    artifact = next(state.glob("profile_*.json"))
    assert IntradayVolumeProfileArtifactStore(state).load(artifact).through_minute == 35
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(artifact).st_mode) == 0o600
    report = (output / cli.REPORT_NAME).read_text(encoding="utf-8")
    assert "new raw page: 0" in report
    assert "account/order mutation: 0" in report
    assert "fixture-secret" not in report


def test_cli_bad_arguments_do_not_load_credentials(monkeypatch) -> None:
    loaded = False

    def load(_path: Path) -> AlpacaCredentials:
        nonlocal loaded
        loaded = True
        return AlpacaCredentials("fixture-key", "fixture-secret")

    monkeypatch.setattr(cli, "load_alpaca_credentials", load)
    try:
        _ = cli.parse_args(("--instrument-id", "only"))
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("incomplete arguments must fail")
    assert loaded is False
