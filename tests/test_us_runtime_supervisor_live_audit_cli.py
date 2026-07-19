from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

import run_us_runtime_supervisor_live_audit as cli
from tests.test_us_runtime_supervisor_live_summary import _parent
from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveOutcome,
    RuntimeSupervisorLiveStatus,
    build_runtime_supervisor_live_audit,
)


def test_help_is_available() -> None:
    with pytest.raises(SystemExit) as raised:
        _ = cli.parse_args(["--help"])
    assert raised.value.code == 0


def test_missing_store_writes_sanitized_blocked_report(tmp_path: Path) -> None:
    report = tmp_path / "report"

    code = cli.main(
        [
            "--supervisor-store",
            str(tmp_path / "missing.sqlite3"),
            "--output-dir",
            str(report),
        ]
    )

    assert code == 1
    assert not (tmp_path / "missing.sqlite3").exists()
    assert "result: blocked" in _report(report)
    assert "account/order mutation: 0" in _report(report)


def test_happy_summary_report_contains_only_aggregate_counts(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.sqlite3"
    store = RuntimeMinuteSupervisorStore(path)
    parent = _parent(1, 0, "a")
    child = build_runtime_supervisor_live_audit(
        parent.attempt_id,
        RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.COMPLETED, 2, 1, 1),
    )
    assert store.append_attempt(parent, child)
    report = tmp_path / "report"

    assert cli.main(["--supervisor-store", str(path), "--output-dir", str(report)]) == 0

    content = _report(report)
    assert "result: ready" in content
    assert "parent count: 1" in content
    assert "completed count: 1" in content
    assert "selected/new/replay: 2/1/1" in content
    assert parent.attempt_id not in content
    assert stat.S_IMODE((report / cli.REPORT_NAME).stat().st_mode) == 0o600


def test_tampered_child_returns_blocked_without_raw_error(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.sqlite3"
    store = RuntimeMinuteSupervisorStore(path)
    parent = _parent(1, 0, "b")
    child = build_runtime_supervisor_live_audit(
        parent.attempt_id,
        RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.DISABLED, 0, 0, 0),
    )
    assert store.append_attempt(parent, child)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER runtime_live_actionability_no_update")
    report = tmp_path / "report"

    assert cli.main(["--supervisor-store", str(path), "--output-dir", str(report)]) == 1

    content = _report(report)
    assert "result: blocked" in content
    assert "trigger" not in content
    assert str(path) not in content


def _report(path: Path) -> str:
    return (path / cli.REPORT_NAME).read_text(encoding="utf-8")
