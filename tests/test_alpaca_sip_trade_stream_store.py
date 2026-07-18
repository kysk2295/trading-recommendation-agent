from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from trading_agent.alpaca_sip_trade_store import StoredAlpacaSipTradeFrame
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipRawControlFrame,
    AlpacaSipStreamTerminalRecord,
    AlpacaSipStreamTerminalStatus,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamProtocolError,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore

_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)
_EPOCH = "a" * 32


def test_stream_store_when_existing_rows_are_mutated_fails_append_only(tmp_path: Path) -> None:
    # Given
    store = _completed_store(tmp_path)

    # When / Then
    with sqlite3.connect(store.path) as database, pytest.raises(sqlite3.IntegrityError):
        _ = database.execute("UPDATE control_frames SET payload=?", (b"[]",))


def test_stream_store_when_database_is_not_private_fails_closed(tmp_path: Path) -> None:
    # Given
    store = _completed_store(tmp_path)
    os.chmod(store.path, 0o644)

    # When / Then
    with pytest.raises(AlpacaSipTradeStreamProtocolError):
        _ = store.load_attestation(_EPOCH)


def test_stream_store_when_raw_control_payload_is_corrupted_fails_closed(tmp_path: Path) -> None:
    # Given
    store = _completed_store(tmp_path)
    with sqlite3.connect(store.path) as database:
        database.executescript(
            "DROP TRIGGER control_frames_no_update;"
            "UPDATE control_frames SET payload=x'5b5d' WHERE sequence=2;"
            "CREATE TRIGGER control_frames_no_update BEFORE UPDATE ON control_frames "
            "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
        )

    # When / Then
    with pytest.raises(AlpacaSipTradeStreamProtocolError):
        _ = store.load_attestation(_EPOCH)


def test_stream_store_when_path_is_broken_symlink_fails_closed(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "stream.sqlite3"
    path.symlink_to(tmp_path / "missing.sqlite3")
    store = AlpacaSipTradeStreamStore(path)

    # When / Then
    with pytest.raises(AlpacaSipTradeStreamProtocolError):
        _ = store.load_attestation(_EPOCH)


def test_stream_store_when_v1_state_is_read_then_written_migrates_without_row_rewrite(
    tmp_path: Path,
) -> None:
    # Given
    store = _completed_store(tmp_path)
    with sqlite3.connect(store.path) as database:
        database.executescript(
            "DROP TRIGGER connection_attempts_no_update;"
            "DROP TRIGGER connection_attempts_no_delete;"
            "DROP TABLE connection_attempts;"
            "PRAGMA user_version=1;"
        )

    # When
    attestation = store.load_attestation(_EPOCH)
    _ = store.append_control(
        AlpacaSipRawControlFrame(
            _EPOCH,
            1,
            _NOW + dt.timedelta(milliseconds=1),
            _connected(),
        )
    )

    # Then
    assert attestation is not None
    with sqlite3.connect(store.path) as database:
        assert database.execute("PRAGMA user_version").fetchone() == (2,)
        assert database.execute("SELECT count(*) FROM connection_attempts").fetchone() == (0,)
        assert database.execute("SELECT count(*) FROM terminal_sessions").fetchone() == (1,)


def _completed_store(tmp_path: Path) -> AlpacaSipTradeStreamStore:
    store = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    controls = (_connected(), _authenticated(), _subscribed())
    for sequence, payload in enumerate(controls, start=1):
        _ = store.append_control(
            AlpacaSipRawControlFrame(
                _EPOCH,
                sequence,
                _NOW + dt.timedelta(milliseconds=sequence),
                payload,
            )
        )
    received_at = _NOW + dt.timedelta(milliseconds=4)
    payload = b'[{"T":"t","S":"AAPL"}]'
    frame = StoredAlpacaSipTradeFrame(
        1,
        "b" * 64,
        _DATE,
        received_at,
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    store.append_data_link(_EPOCH, 1, frame)
    store.append_terminal(
        AlpacaSipStreamTerminalRecord(
            _EPOCH,
            AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
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
