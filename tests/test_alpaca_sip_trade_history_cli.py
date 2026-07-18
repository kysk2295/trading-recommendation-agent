from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

_ROOT = Path(__file__).parents[1]
_SCRIPT = _ROOT / "run_alpaca_sip_trade_history_fixture.py"


def test_cli_when_help_is_requested_describes_local_fixture_surface() -> None:
    # Given / When
    completed = _run("--help")

    # Then
    assert completed.returncode == 0
    assert "--input" in completed.stdout
    assert "--store" in completed.stdout
    assert "--output-root" in completed.stdout


def test_cli_when_fixture_is_invalid_stops_before_store_creation(tmp_path: Path) -> None:
    # Given
    fixture = tmp_path / "invalid.json"
    fixture.write_text("{}", encoding="utf-8")
    store = tmp_path / "raw.sqlite3"

    # When
    completed = _run(
        "--input",
        str(fixture),
        "--store",
        str(store),
        "--output-root",
        str(tmp_path / "canonical"),
    )

    # Then
    assert completed.returncode == 1
    assert completed.stderr.strip() == "Alpaca SIP trade history fixture is invalid"
    assert not store.exists()


def test_cli_when_fixture_is_valid_persists_raw_and_publishes_history(tmp_path: Path) -> None:
    # Given
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_fixture()), encoding="utf-8")
    store = tmp_path / "raw.sqlite3"
    output = tmp_path / "canonical"

    # When
    completed = _run(
        "--input",
        str(fixture),
        "--store",
        str(store),
        "--output-root",
        str(output),
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary == {
        "active_trade_count": 0,
        "correction_count": 1,
        "dataset_id": summary["dataset_id"],
        "event_count": 3,
        "history_complete": False,
        "history_reason_codes": ["continuity_unattested"],
        "network_request_count": 0,
        "raw_frame_count": 1,
        "tombstone_count": 1,
    }
    assert len(summary["dataset_id"]) == 64
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert len(tuple(output.rglob("events.parquet"))) == 1


def test_cli_when_stream_store_is_requested_attests_bounded_history(tmp_path: Path) -> None:
    # Given
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_fixture()), encoding="utf-8")
    stream_store = tmp_path / "stream.sqlite3"

    # When
    completed = _run(
        "--input",
        str(fixture),
        "--store",
        str(tmp_path / "raw.sqlite3"),
        "--stream-store",
        str(stream_store),
        "--output-root",
        str(tmp_path / "canonical"),
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["history_complete"] is True
    assert summary["history_reason_codes"] == []
    assert summary["stream_control_count"] == 3
    assert summary["stream_data_link_count"] == 1
    assert stat.S_IMODE(stream_store.stat().st_mode) == 0o600


def test_cli_when_reconnect_is_simulated_preserves_gap_as_incomplete(tmp_path: Path) -> None:
    # Given
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_reconnect_fixture()), encoding="utf-8")

    # When
    completed = _run(
        "--input",
        str(fixture),
        "--store",
        str(tmp_path / "raw.sqlite3"),
        "--stream-store",
        str(tmp_path / "stream.sqlite3"),
        "--simulate-reconnect-after",
        "1",
        "--output-root",
        str(tmp_path / "canonical"),
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["history_complete"] is False
    assert summary["history_reason_codes"] == ["continuity_unattested"]
    assert summary["stream_session_count"] == 2
    assert summary["stream_failed_session_count"] == 1
    assert summary["stream_control_count"] == 6
    assert summary["stream_data_link_count"] == 2


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(_SCRIPT), *arguments),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _fixture() -> dict[str, int | str | list[dict[str, str]]]:
    payload = json.dumps(
        (
            {
                "T": "t",
                "S": "AAPL",
                "i": 101,
                "x": "V",
                "p": 211.25,
                "s": 40,
                "c": ["@"],
                "t": "2026-07-17T14:29:59.123456Z",
                "z": "C",
            },
            {
                "T": "c",
                "S": "AAPL",
                "x": "V",
                "oi": 101,
                "op": 211.25,
                "os": 40,
                "oc": ["@"],
                "ci": 102,
                "cp": 211.2,
                "cs": 35,
                "cc": ["@"],
                "t": "2026-07-17T14:29:59.123456Z",
                "z": "C",
            },
            {
                "T": "x",
                "S": "AAPL",
                "i": 102,
                "x": "V",
                "p": 211.2,
                "s": 35,
                "a": "C",
                "t": "2026-07-17T14:29:59.123456Z",
                "z": "C",
            },
        ),
        separators=(",", ":"),
    ).encode()
    return {
        "schema_version": 1,
        "market_date": "2026-07-17",
        "symbol": "AAPL",
        "instrument_id": "us-equity-aapl",
        "frames": [
            {
                "received_at": dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC).isoformat(),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "payload_base64": base64.b64encode(payload).decode("ascii"),
            }
        ],
    }


def _reconnect_fixture() -> dict[str, Any]:
    fixture = cast(dict[str, Any], _fixture())
    frame = fixture["frames"][0]
    messages = json.loads(base64.b64decode(frame["payload_base64"]))
    fixture["frames"] = [
        _encoded_frame(messages[:1], dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)),
        _encoded_frame(messages[1:], dt.datetime(2026, 7, 17, 14, 30, 1, tzinfo=dt.UTC)),
    ]
    return fixture


def _encoded_frame(messages: list[dict[str, Any]], received_at: dt.datetime) -> dict[str, str]:
    payload = json.dumps(messages, separators=(",", ":")).encode()
    return {
        "received_at": received_at.isoformat(),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_base64": base64.b64encode(payload).decode("ascii"),
    }
