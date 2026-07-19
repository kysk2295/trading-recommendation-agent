from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from pathlib import Path

import pytest

from trading_agent.us_runtime_minute_supervisor import (
    RuntimeMinuteSupervisorConfig,
    RuntimeSupervisorOperationBlockedError,
    RuntimeSupervisorOperationResult,
    RuntimeSupervisorStatus,
    run_runtime_minute_supervisor,
)
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore

NY = dt.timezone(dt.timedelta(hours=-4))
START = dt.datetime(2026, 7, 17, 10, 5, tzinfo=NY)


def test_blocked_cycle_is_audited_and_next_minute_recovers(tmp_path: Path) -> None:
    times = iter(
        (
            START,
            START + dt.timedelta(seconds=1),
            START + dt.timedelta(minutes=1),
            START + dt.timedelta(minutes=1, seconds=1),
        )
    )
    waits: list[float] = []
    calls: list[dt.datetime] = []

    def operation(evaluated_at: dt.datetime) -> RuntimeSupervisorOperationResult:
        calls.append(evaluated_at)
        if len(calls) == 1:
            raise RuntimeSupervisorOperationBlockedError
        return RuntimeSupervisorOperationResult("a" * 64, True)

    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    records = run_runtime_minute_supervisor(
        operation,
        RuntimeMinuteSupervisorConfig(2, 60.0),
        clock=lambda: next(times),
        sleeper=waits.append,
        writer=store,
    )

    assert tuple(item.status for item in records) == (
        RuntimeSupervisorStatus.BLOCKED,
        RuntimeSupervisorStatus.READY,
    )
    assert tuple(item.cycle_index for item in records) == (1, 2)
    assert records[0].fleet_cycle_id is None
    assert records[1].fleet_cycle_id == "a" * 64
    assert waits == [60.0]
    assert store.records() == records
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_session_close_stops_before_another_operation(tmp_path: Path) -> None:
    times = iter(
        (
            dt.datetime(2026, 7, 17, 15, 59, tzinfo=NY),
            dt.datetime(2026, 7, 17, 15, 59, 1, tzinfo=NY),
            dt.datetime(2026, 7, 17, 16, 0, tzinfo=NY),
        )
    )
    calls: list[dt.datetime] = []
    records = run_runtime_minute_supervisor(
        lambda value: calls.append(value) or RuntimeSupervisorOperationResult("b" * 64, True),
        RuntimeMinuteSupervisorConfig(3, 60.0),
        clock=lambda: next(times),
        sleeper=lambda _seconds: None,
        writer=RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3"),
    )

    assert len(records) == 1
    assert calls == [dt.datetime(2026, 7, 17, 15, 59, tzinfo=NY)]


def test_shutdown_after_cycle_stops_before_wait_or_next_operation(tmp_path: Path) -> None:
    requested = False
    calls: list[dt.datetime] = []
    waits: list[float] = []
    times = iter((START, START + dt.timedelta(seconds=1)))

    def operation(value: dt.datetime) -> RuntimeSupervisorOperationResult:
        nonlocal requested
        calls.append(value)
        requested = True
        return RuntimeSupervisorOperationResult("e" * 64, True)

    records = run_runtime_minute_supervisor(
        operation,
        RuntimeMinuteSupervisorConfig(3, 60.0),
        clock=lambda: next(times),
        sleeper=waits.append,
        writer=RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3"),
        shutdown_requested=lambda: requested,
    )

    assert len(records) == 1
    assert calls == [START]
    assert waits == []


def test_tampered_supervisor_payload_fails_replay(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    times = iter((START, START + dt.timedelta(seconds=1)))
    _ = run_runtime_minute_supervisor(
        lambda _value: RuntimeSupervisorOperationResult("c" * 64, True),
        RuntimeMinuteSupervisorConfig(1, 60.0),
        clock=lambda: next(times),
        sleeper=lambda _seconds: None,
        writer=store,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER runtime_minute_supervisor_no_update")
        connection.execute("UPDATE runtime_minute_supervisor SET payload_json=X'7B7D'")
        connection.commit()

    with pytest.raises(ValueError, match="supervisor"):
        _ = store.records()


def test_public_or_symlinked_supervisor_store_fails_closed(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    times = iter((START, START + dt.timedelta(seconds=1)))
    _ = run_runtime_minute_supervisor(
        lambda _value: RuntimeSupervisorOperationResult("d" * 64, True),
        RuntimeMinuteSupervisorConfig(1, 60.0),
        clock=lambda: next(times),
        sleeper=lambda _seconds: None,
        writer=store,
    )
    store.path.chmod(0o640)
    with pytest.raises(ValueError, match="supervisor"):
        _ = store.records()

    linked = tmp_path / "linked.sqlite3"
    linked.symlink_to(store.path)
    with pytest.raises(ValueError, match="supervisor"):
        _ = RuntimeMinuteSupervisorStore(linked).records()
