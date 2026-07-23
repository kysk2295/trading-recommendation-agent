from __future__ import annotations

import datetime as dt
import hashlib
import json
import stat
from pathlib import Path

import pytest

import run_alpaca_sip_entitlement_admission as cli
from trading_agent.alpaca_sip_trade_store import StoredAlpacaSipTradeFrame
from trading_agent.alpaca_sip_trade_stream_attempts import (
    AlpacaSipConnectionAttemptStage,
    AlpacaSipConnectionAttemptStore,
    AlpacaSipConnectionFailureCode,
    AlpacaSipFailedConnectionAttempt,
)
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipRawControlFrame,
    AlpacaSipStreamTerminalRecord,
    AlpacaSipStreamTerminalStatus,
    AlpacaSipTradeStreamConfig,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore

_DATE = dt.date(2026, 7, 23)
_NOW = dt.datetime(2026, 7, 23, 13, 35, tzinfo=dt.UTC)
_CONFIG = AlpacaSipTradeStreamConfig(_DATE, "AAPL")


def test_help_exposes_query_only_entitlement_admission() -> None:
    # Given / When
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])

    # Then
    assert raised.value.code == 0


def test_insufficient_subscription_is_durable_block_and_exact_replay(
    tmp_path: Path,
) -> None:
    # Given
    stream_store = tmp_path / "stream.sqlite3"
    output = tmp_path / "output"
    _append_attempt(
        stream_store,
        failure_code=AlpacaSipConnectionFailureCode.INSUFFICIENT_SUBSCRIPTION,
    )

    # When
    first = cli.main(_arguments(stream_store, output))
    first_artifact = next(output.glob("alpaca_sip_entitlement_*.json"))
    first_payload = json.loads(first_artifact.read_text())
    second = cli.main(_arguments(stream_store, output))
    second_report = (output / cli.REPORT_NAME).read_text()

    # Then
    assert first == second == 2
    assert first_payload["status"] == "blocked"
    assert first_payload["reason_code"] == "insufficient_subscription"
    assert first_payload["source_id"] == "alpaca/sip"
    assert len(first_payload["evidence_sha256"]) == 64
    assert "artifact created: false" in second_report
    assert len(tuple(output.glob("alpaca_sip_entitlement_*.json"))) == 1
    assert stat.S_IMODE(first_artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE((output / cli.REPORT_NAME).stat().st_mode) == 0o600


def test_bounded_complete_session_is_ready(tmp_path: Path) -> None:
    # Given
    stream_store = _completed_store(tmp_path)
    output = tmp_path / "output"

    # When
    result = cli.main(_arguments(stream_store.path, output))
    artifact = next(output.glob("alpaca_sip_entitlement_*.json"))
    payload = json.loads(artifact.read_text())

    # Then
    assert result == 0
    assert payload["status"] == "ready"
    assert payload["reason_code"] == "bounded_complete"
    assert "result: ready" in (output / cli.REPORT_NAME).read_text()


def test_transient_failure_stays_unknown_without_artifact(tmp_path: Path) -> None:
    # Given
    stream_store = tmp_path / "stream.sqlite3"
    output = tmp_path / "output"
    _append_attempt(
        stream_store,
        failure_code=AlpacaSipConnectionFailureCode.CONNECTION_LIMIT,
    )

    # When
    result = cli.main(_arguments(stream_store, output))
    report = (output / cli.REPORT_NAME).read_text()

    # Then
    assert result == 1
    assert "result: unknown" in report
    assert "reason: transient_or_missing_evidence" in report
    assert not tuple(output.glob("alpaca_sip_entitlement_*.json"))


def test_cli_has_no_provider_or_order_authority_imports() -> None:
    # Given / When
    source = Path(cli.__file__).read_text()

    # Then
    assert "alpaca_http" not in source
    assert "alpaca_sip_trade_stream import" not in source
    assert "credentials" not in source.lower()
    assert "order" not in source.lower()


def _arguments(stream_store: Path, output: Path) -> tuple[str, ...]:
    return (
        "--stream-store",
        str(stream_store),
        "--symbol",
        _CONFIG.symbol,
        "--market-date",
        _CONFIG.market_date.isoformat(),
        "--output-dir",
        str(output),
    )


def _append_attempt(
    path: Path,
    *,
    failure_code: AlpacaSipConnectionFailureCode,
) -> None:
    epoch = "a" * 32
    store = AlpacaSipTradeStreamStore(path)
    if failure_code is AlpacaSipConnectionFailureCode.INSUFFICIENT_SUBSCRIPTION:
        controls = (_connected(), b'[{"T":"error","code":409,"msg":"provider detail"}]')
        stage = AlpacaSipConnectionAttemptStage.AUTHENTICATION_CONTROL
    else:
        controls = (b'[{"T":"error","code":406,"msg":"provider detail"}]',)
        stage = AlpacaSipConnectionAttemptStage.CONNECTED_CONTROL
    for sequence, payload in enumerate(controls, start=1):
        _ = store.append_control(
            AlpacaSipRawControlFrame(
                epoch,
                sequence,
                _NOW + dt.timedelta(milliseconds=sequence),
                payload,
            )
        )
    AlpacaSipConnectionAttemptStore(path).append(
        AlpacaSipFailedConnectionAttempt(
            connection_epoch=epoch,
            config=_CONFIG,
            failed_at=_NOW,
            stage=stage,
            failure_code=failure_code,
        )
    )


def _completed_store(tmp_path: Path) -> AlpacaSipTradeStreamStore:
    store = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    epoch = "b" * 32
    controls = (_connected(), _authenticated(), _subscribed())
    for sequence, payload in enumerate(controls, start=1):
        _ = store.append_control(
            AlpacaSipRawControlFrame(
                epoch,
                sequence,
                _NOW + dt.timedelta(milliseconds=sequence),
                payload,
            )
        )
    received_at = _NOW + dt.timedelta(milliseconds=4)
    payload = b'[{"T":"t","S":"AAPL"}]'
    store.append_data_link(
        epoch,
        1,
        StoredAlpacaSipTradeFrame(
            1,
            "c" * 64,
            _DATE,
            received_at,
            hashlib.sha256(payload).hexdigest(),
            payload,
        ),
    )
    store.append_terminal(
        AlpacaSipStreamTerminalRecord(
            epoch,
            _CONFIG,
            _NOW + dt.timedelta(milliseconds=2),
            _NOW + dt.timedelta(milliseconds=3),
            _NOW + dt.timedelta(milliseconds=5),
            AlpacaSipStreamTerminalStatus.BOUNDED_COMPLETE,
        )
    )
    return store


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
