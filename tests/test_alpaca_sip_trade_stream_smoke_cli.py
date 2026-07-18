from __future__ import annotations

import datetime as dt
import json
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import run_alpaca_sip_trade_stream_smoke as cli
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_stream import ALPACA_SIP_TRADE_STREAM_URL
from trading_agent.us_equity_calendar import NEW_YORK

_ROOT = Path(__file__).parents[1]
_SCRIPT = _ROOT / "run_alpaca_sip_trade_stream_smoke.py"


def test_cli_when_help_is_requested_describes_bounded_read_only_surface() -> None:
    # Given / When
    completed = subprocess.run(
        (sys.executable, str(_SCRIPT), "--help"),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0
    assert "--arm-read-only" in completed.stdout
    assert "--max-frames" in completed.stdout
    assert "--state-dir" in completed.stdout


def test_cli_when_regular_session_is_closed_stops_before_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    credential_calls = 0

    def unexpected_credentials(_: Path) -> AlpacaCredentials:
        nonlocal credential_calls
        credential_calls += 1
        raise AssertionError("credentials must remain unread")

    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 7, 19, 14, 30, tzinfo=dt.UTC), raising=False)
    monkeypatch.setattr(cli, "_load_private_credentials", unexpected_credentials, raising=False)
    state = tmp_path / "state"

    # When
    result = cli.main(_arguments(state))

    # Then
    assert result == 1
    assert credential_calls == 0
    assert not state.exists()


def test_cli_when_read_only_arm_is_missing_stops_before_clock_or_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    clock_calls = 0
    credential_calls = 0

    def unexpected_clock() -> dt.datetime:
        nonlocal clock_calls
        clock_calls += 1
        raise AssertionError("clock must remain unread")

    def unexpected_credentials(_: Path) -> AlpacaCredentials:
        nonlocal credential_calls
        credential_calls += 1
        raise AssertionError("credentials must remain unread")

    monkeypatch.setattr(cli, "_utc_now", unexpected_clock)
    monkeypatch.setattr(cli, "_load_private_credentials", unexpected_credentials)
    arguments = _arguments(tmp_path / "state")[:-1]

    # When
    result = cli.main(arguments)

    # Then
    assert result == 1
    assert clock_calls == 0
    assert credential_calls == 0


def test_cli_when_bounded_fixture_is_valid_publishes_complete_private_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    connection = FakeSipConnection([_connected(), _authenticated(), _subscribed(), _trade_chain()])
    opened = dt.datetime(2026, 7, 17, 10, 30, tzinfo=NEW_YORK)
    times = iter(opened + dt.timedelta(microseconds=index) for index in range(8))
    monkeypatch.setattr(cli, "_utc_now", times.__next__, raising=False)
    monkeypatch.setattr(
        cli,
        "_load_private_credentials",
        lambda _: AlpacaCredentials("fixture-key", "fixture-secret"),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "connect_alpaca_sip_trade_stream",
        _connector(connection),
        raising=False,
    )
    state = tmp_path / "state"

    # When
    result = cli.main(_arguments(state))
    output = capsys.readouterr()

    # Then
    assert result == 0, output.err
    summary = json.loads(output.out)
    assert summary["history_complete"] is True
    assert summary["frame_count"] == 1
    assert summary["event_count"] == 3
    assert summary["stream_control_count"] == 3
    assert summary["stream_data_link_count"] == 1
    assert summary["broker_mutation_count"] == 0
    assert summary["websocket_connection_count"] == 1
    assert "fixture-secret" not in output.out + output.err
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / cli.REPORT_NAME).stat().st_mode) == 0o600
    assert len(tuple((state / "canonical").rglob("events.parquet"))) == 1


def test_cli_when_session_closes_after_frame_records_failed_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    connection = FakeSipConnection([_connected(), _authenticated(), _subscribed(), _trade_chain()])
    opened = dt.datetime(2026, 7, 17, 15, 59, 59, tzinfo=NEW_YORK)
    closed = dt.datetime(2026, 7, 17, 16, 0, tzinfo=NEW_YORK)
    times = iter((opened, opened, opened, opened, opened, closed, closed))
    monkeypatch.setattr(cli, "_utc_now", times.__next__)
    monkeypatch.setattr(
        cli,
        "_load_private_credentials",
        lambda _: AlpacaCredentials("fixture-key", "fixture-secret"),
    )
    monkeypatch.setattr(cli, "connect_alpaca_sip_trade_stream", _connector(connection))
    state = tmp_path / "state"

    # When
    result = cli.main(_arguments(state))
    output = capsys.readouterr()

    # Then
    assert result == 2
    with sqlite3.connect(state / "stream.sqlite3") as database:
        statuses = tuple(row[0] for row in database.execute("SELECT status FROM terminal_sessions"))
    assert statuses == ("failed",)
    assert not (state / cli.REPORT_NAME).exists()
    assert output.err.strip() == "Alpaca SIP trade stream smoke failed"
    assert "fixture-secret" not in output.out + output.err


def test_private_credential_loader_when_path_is_symlink_fails_closed(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "alpaca.env"
    target.write_text("APCA_API_KEY_ID=x\nAPCA_API_SECRET_KEY=y\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "linked.env"
    link.symlink_to(target)

    # When / Then
    with pytest.raises(cli.AlpacaSipTradeStreamSmokeError):
        _ = cli._load_private_credentials(link)


def test_cli_when_state_root_is_not_private_stops_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    opened = dt.datetime(2026, 7, 17, 10, 30, tzinfo=NEW_YORK)
    network_calls = 0

    @contextmanager
    def unexpected_connector(_: str) -> Iterator[FakeSipConnection]:
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("network must remain unopened")
        yield FakeSipConnection([])

    monkeypatch.setattr(cli, "_utc_now", lambda: opened)
    monkeypatch.setattr(
        cli,
        "_load_private_credentials",
        lambda _: AlpacaCredentials("fixture-key", "fixture-secret"),
    )
    monkeypatch.setattr(cli, "connect_alpaca_sip_trade_stream", unexpected_connector)

    # When
    result = cli.main(_arguments(state))

    # Then
    assert result == 2
    assert network_calls == 0


class FakeSipConnection:
    __slots__ = ("_responses", "final_url")

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = responses
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return self._responses.pop(0)


def _arguments(state: Path) -> tuple[str, ...]:
    return (
        "--instrument-id",
        "us-equity-aapl",
        "--symbol",
        "AAPL",
        "--state-dir",
        str(state),
        "--max-frames",
        "1",
        "--arm-read-only",
    )


def _connector(connection: FakeSipConnection):
    @contextmanager
    def connector(_: str) -> Iterator[FakeSipConnection]:
        yield connection

    return connector


def _connected() -> bytes:
    return b'[{"T":"success","msg":"connected"}]'


def _authenticated() -> bytes:
    return b'[{"T":"success","msg":"authenticated"}]'


def _subscribed() -> bytes:
    return (
        b'[{"T":"subscription","trades":["AAPL"],"quotes":[],"bars":[],"updatedBars":[],'
        b'"dailyBars":[],"statuses":[],"lulds":[],"corrections":["AAPL"],'
        b'"cancelErrors":["AAPL"]}]'
    )


def _trade_chain() -> bytes:
    return (
        b'[{"T":"t","S":"AAPL","i":101,"x":"V","p":211.25,"s":40,"c":["@"],'
        b'"t":"2026-07-17T14:29:59.123456Z","z":"C"},'
        b'{"T":"c","S":"AAPL","x":"V","oi":101,"op":211.25,"os":40,"oc":["@"],'
        b'"ci":102,"cp":211.2,"cs":35,"cc":["@"],"t":"2026-07-17T14:29:59.123456Z","z":"C"},'
        b'{"T":"x","S":"AAPL","i":102,"x":"V","p":211.2,"s":35,"a":"C",'
        b'"t":"2026-07-17T14:29:59.123456Z","z":"C"}]'
    )
