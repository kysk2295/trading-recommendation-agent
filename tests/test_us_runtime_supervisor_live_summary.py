from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.test_us_runtime_minute_supervisor import START
from trading_agent.us_runtime_minute_supervisor import (
    RuntimeMinuteSupervisorRecord,
    RuntimeSupervisorStatus,
    build_runtime_minute_supervisor_record,
)
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveOutcome,
    RuntimeSupervisorLiveStatus,
    build_runtime_supervisor_live_audit,
)
from trading_agent.us_runtime_supervisor_live_summary import (
    RuntimeSupervisorLiveSummaryError,
    summarize_runtime_supervisor_live_audit,
)


def test_summary_separates_legacy_parent_and_live_outcomes(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    legacy = _parent(1, 0, "a")
    assert store.append(legacy)
    current = _parent(2, 1, "b")
    child = build_runtime_supervisor_live_audit(
        current.attempt_id,
        RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.COMPLETED, 2, 1, 1),
    )
    assert store.append_attempt(current, child)

    summary = summarize_runtime_supervisor_live_audit(store)

    assert (summary.parent_count, summary.legacy_parent_count, summary.child_count) == (2, 1, 1)
    assert (summary.completed_count, summary.blocked_count) == (1, 0)
    assert (summary.selected_count, summary.created_count, summary.replay_count) == (2, 1, 1)


def test_empty_store_has_zero_summary_without_creating_file(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "missing.sqlite3")

    summary = summarize_runtime_supervisor_live_audit(store)

    assert summary.parent_count == 0
    assert summary.child_count == 0
    assert not store.path.exists()


def test_non_suffix_child_history_is_rejected(tmp_path: Path) -> None:
    store = RuntimeMinuteSupervisorStore(tmp_path / "supervisor.sqlite3")
    first = _parent(1, 0, "c")
    first_child = build_runtime_supervisor_live_audit(
        first.attempt_id,
        RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.DISABLED, 0, 0, 0),
    )
    assert store.append_attempt(first, first_child)
    assert store.append(_parent(2, 1, "d"))

    with pytest.raises(RuntimeSupervisorLiveSummaryError):
        _ = summarize_runtime_supervisor_live_audit(store)


def _parent(index: int, minute: int, cycle: str) -> RuntimeMinuteSupervisorRecord:
    at = START + dt.timedelta(minutes=minute)
    return build_runtime_minute_supervisor_record(
        index,
        at,
        at,
        RuntimeSupervisorStatus.READY,
        None,
        cycle * 64,
    )
