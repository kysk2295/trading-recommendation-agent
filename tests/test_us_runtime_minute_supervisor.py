from __future__ import annotations

import datetime as dt
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from trading_agent.us_runtime_minute_supervisor import (
    RuntimeMinuteSupervisorConfig,
    RuntimeSupervisorOperationBlockedError,
    RuntimeSupervisorOperationResult,
    RuntimeSupervisorStatus,
    build_runtime_minute_supervisor_record,
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


def test_restart_resumes_market_date_cycle_index_and_budget(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    requested = False
    first_times = iter((START, START + dt.timedelta(seconds=1)))

    def first_operation(_value: dt.datetime) -> RuntimeSupervisorOperationResult:
        nonlocal requested
        requested = True
        return RuntimeSupervisorOperationResult("f" * 64, True)

    first = run_runtime_minute_supervisor(
        first_operation,
        RuntimeMinuteSupervisorConfig(2, 60.0),
        clock=lambda: next(first_times),
        sleeper=lambda _seconds: None,
        writer=store,
        shutdown_requested=lambda: requested,
    )
    second_times = iter((START + dt.timedelta(minutes=1), START + dt.timedelta(minutes=1, seconds=1)))
    second = run_runtime_minute_supervisor(
        lambda _value: RuntimeSupervisorOperationResult("1" * 64, True),
        RuntimeMinuteSupervisorConfig(2, 60.0),
        clock=lambda: next(second_times),
        sleeper=lambda _seconds: None,
        writer=store,
    )
    final_calls = 0

    def final_clock() -> dt.datetime:
        nonlocal final_calls
        final_calls += 1
        return START + dt.timedelta(minutes=2)

    exhausted = run_runtime_minute_supervisor(
        lambda _value: RuntimeSupervisorOperationResult("2" * 64, True),
        RuntimeMinuteSupervisorConfig(2, 60.0),
        clock=final_clock,
        sleeper=lambda _seconds: None,
        writer=store,
    )

    assert tuple(item.cycle_index for item in first + second) == (1, 2)
    assert store.records() == first + second
    assert exhausted == ()
    assert final_calls == 1


def test_restart_with_duplicate_market_date_cycle_index_fails_closed(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    assert store.append(
        build_runtime_minute_supervisor_record(
            1,
            START,
            START + dt.timedelta(seconds=1),
            RuntimeSupervisorStatus.READY,
            None,
            "3" * 64,
        )
    )
    assert store.append(
        build_runtime_minute_supervisor_record(
            1,
            START + dt.timedelta(minutes=1),
            START + dt.timedelta(minutes=1, seconds=1),
            RuntimeSupervisorStatus.READY,
            None,
            "4" * 64,
        )
    )

    with pytest.raises(ValueError, match="supervisor"):
        _ = run_runtime_minute_supervisor(
            lambda _value: RuntimeSupervisorOperationResult("5" * 64, True),
            RuntimeMinuteSupervisorConfig(2, 60.0),
            clock=lambda: START + dt.timedelta(minutes=2),
            sleeper=lambda _seconds: None,
            writer=store,
        )


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


def test_missing_append_only_trigger_fails_replay(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    times = iter((START, START + dt.timedelta(seconds=1)))
    _ = run_runtime_minute_supervisor(
        lambda _value: RuntimeSupervisorOperationResult("6" * 64, True),
        RuntimeMinuteSupervisorConfig(1, 60.0),
        clock=lambda: next(times),
        sleeper=lambda _seconds: None,
        writer=store,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER runtime_minute_supervisor_no_update")

    with pytest.raises(ValueError, match="supervisor"):
        _ = store.records()


def test_hardlinked_supervisor_store_fails_replay(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    times = iter((START, START + dt.timedelta(seconds=1)))
    _ = run_runtime_minute_supervisor(
        lambda _value: RuntimeSupervisorOperationResult("7" * 64, True),
        RuntimeMinuteSupervisorConfig(1, 60.0),
        clock=lambda: next(times),
        sleeper=lambda _seconds: None,
        writer=store,
    )
    os.link(store.path, tmp_path / "alias.sqlite3")

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
