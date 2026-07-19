from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.test_us_runtime_minute_supervisor import START
from trading_agent.us_runtime_fleet_cycle_cli_result import LIVE_BLOCKED
from trading_agent.us_runtime_minute_supervisor import (
    RuntimeMinuteSupervisorConfig,
    RuntimeMinuteSupervisorError,
    RuntimeSupervisorOperationBlockedError,
    RuntimeSupervisorStatus,
    build_runtime_minute_supervisor_record,
    run_runtime_minute_supervisor,
)
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveAuditError,
    RuntimeSupervisorLiveOutcome,
    RuntimeSupervisorLiveStatus,
    build_runtime_supervisor_live_audit,
    live_audit_bytes,
    live_audit_from_bytes,
)


def test_live_audit_round_trip_preserves_aggregate_without_market_values() -> None:
    audit = build_runtime_supervisor_live_audit(
        "a" * 64,
        RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.COMPLETED, 2, 1, 1),
    )

    assert live_audit_from_bytes(live_audit_bytes(audit)) == audit
    assert set(live_audit_bytes(audit)) <= set(range(128))


@pytest.mark.parametrize(
    ("status", "counts"),
    (
        (RuntimeSupervisorLiveStatus.DISABLED, (1, 0, 0)),
        (RuntimeSupervisorLiveStatus.NOT_ATTEMPTED, (0, 1, 0)),
        (RuntimeSupervisorLiveStatus.BLOCKED, (0, 0, 1)),
        (RuntimeSupervisorLiveStatus.COMPLETED, (2, 1, 0)),
    ),
)
def test_invalid_live_outcome_counts_are_rejected(
    status: RuntimeSupervisorLiveStatus,
    counts: tuple[int, int, int],
) -> None:
    with pytest.raises(RuntimeSupervisorLiveAuditError):
        _ = RuntimeSupervisorLiveOutcome(status, *counts)


def test_store_migrates_v1_without_rewriting_attempt_and_appends_child_atomically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "supervisor.sqlite3"
    store = RuntimeMinuteSupervisorStore(path)
    legacy = build_runtime_minute_supervisor_record(
        1,
        START,
        START,
        RuntimeSupervisorStatus.READY,
        None,
        "b" * 64,
    )
    assert store.append(legacy)
    with sqlite3.connect(path) as connection:
        before: tuple[bytes] = connection.execute(
            "SELECT payload_json FROM runtime_minute_supervisor WHERE attempt_id=?",
            (legacy.attempt_id,),
        ).fetchone()
        connection.executescript(
            "DROP TRIGGER runtime_live_actionability_no_update;"
            "DROP TRIGGER runtime_live_actionability_no_delete;"
            "DROP TABLE runtime_live_actionability;"
            "PRAGMA user_version=1;"
        )
    assert store.records() == (legacy,)
    assert store.live_records() == ()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    current = build_runtime_minute_supervisor_record(
        2,
        START.replace(minute=START.minute + 1),
        START.replace(minute=START.minute + 1),
        RuntimeSupervisorStatus.READY,
        None,
        "c" * 64,
    )
    child = build_runtime_supervisor_live_audit(
        current.attempt_id,
        RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.COMPLETED, 1, 1, 0),
    )

    assert store.append_attempt(current, child)

    assert store.records() == (legacy, current)
    assert store.live_records() == (child,)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert (
            connection.execute(
                "SELECT payload_json FROM runtime_minute_supervisor WHERE attempt_id=?",
                (legacy.attempt_id,),
            ).fetchone()
            == before
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE runtime_live_actionability SET selected_count=9")


def test_blocked_operation_persists_parent_and_live_child_atomically(tmp_path: Path) -> None:
    times = iter((START, START.replace(second=1)))
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")

    records = run_runtime_minute_supervisor(
        lambda _value: (_ for _ in ()).throw(RuntimeSupervisorOperationBlockedError(LIVE_BLOCKED)),
        RuntimeMinuteSupervisorConfig(1, 60.0),
        clock=lambda: next(times),
        sleeper=lambda _seconds: None,
        writer=store,
    )

    assert records[0].status is RuntimeSupervisorStatus.BLOCKED
    assert store.live_records()[0].status is RuntimeSupervisorLiveStatus.BLOCKED
    assert store.live_records()[0].attempt_id == records[0].attempt_id


def test_tampered_live_child_fails_parent_bound_replay(tmp_path: Path) -> None:
    times = iter((START, START.replace(second=1)))
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    _ = run_runtime_minute_supervisor(
        lambda _value: (_ for _ in ()).throw(RuntimeSupervisorOperationBlockedError(LIVE_BLOCKED)),
        RuntimeMinuteSupervisorConfig(1, 60.0),
        clock=lambda: next(times),
        sleeper=lambda _seconds: None,
        writer=store,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER runtime_live_actionability_no_update")
        connection.execute("UPDATE runtime_live_actionability SET payload_json=X'7B7D'")
        connection.commit()

    with pytest.raises(RuntimeMinuteSupervisorError):
        _ = store.live_records()
