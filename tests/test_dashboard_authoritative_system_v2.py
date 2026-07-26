from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.dashboard_system_evidence import MILESTONE_FILE, MILESTONE_IDS
from trading_agent.dashboard_system_operations import OPERATIONS_FILE

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)


def test_system_projects_exactly_m0_through_m10_from_typed_evidence(
    tmp_path: Path,
) -> None:
    # Given complete allowlisted typed milestone and operation evidence
    root = tmp_path / "outputs/system"
    root.mkdir(parents=True)
    _write_milestones(root, NOW)
    _write_operations(root, NOW)

    # When system evidence is projected
    snapshot = collect_dashboard_snapshot_v2(tmp_path / "outputs", now=NOW)
    system = snapshot.workspaces.system

    # Then exactly eleven milestones precede the three typed operations
    assert snapshot.workspaces.command_center.agents == ()
    assert system.state == "populated"
    assert tuple(item.label for item in system.items[:11]) == MILESTONE_IDS
    assert len(system.items) == 14


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ("launchd_stale", "launchd_pid_stale"),
        ("deployment_mismatch", "deployment_sha_mismatch"),
        ("relay_stale", "relay_receipt_stale"),
    ],
)
def test_system_operations_fail_closed_from_typed_receipts(
    tmp_path: Path,
    mutation: str,
    blocker: str,
) -> None:
    # Given one typed operational authority is stale or inconsistent
    root = tmp_path / "outputs/system"
    root.mkdir(parents=True)
    _write_milestones(root, NOW)
    _write_operations(root, NOW, mutation=mutation)

    # When system operations are projected
    snapshot = collect_dashboard_snapshot_v2(tmp_path / "outputs", now=NOW)

    # Then the exact normalized blocker is emitted without process inference
    assert snapshot.workspaces.system.blocker_code == blocker
    operation_items = tuple(
        item
        for item in snapshot.workspaces.system.items
        if item.item_id.startswith("system.operation.")
    )
    assert any(item.state == "blocked" for item in operation_items)
    assert any(
        node.kind == "blocker_terminal"
        for node in snapshot.traces.nodes
        if node.source_namespace == "system.operations"
    )


def test_system_operations_reject_arbitrary_log_and_path_canary(tmp_path: Path) -> None:
    # Given an operation receipt adds prohibited raw log and local path fields
    root = tmp_path / "outputs/system"
    root.mkdir(parents=True)
    path = root / OPERATIONS_FILE
    hostile = {**_operation_rows(NOW)[0], "raw_log": "success /Users/canary secret_token"}
    path.write_text(json.dumps(hostile), encoding="utf-8")
    path.chmod(0o600)

    # When the strict operation reader parses it
    snapshot = collect_dashboard_snapshot_v2(tmp_path / "outputs", now=NOW)

    # Then the section fails closed without transmitting the canary
    serialized = snapshot.model_dump_json().lower()
    assert snapshot.workspaces.system.state == "corrupt"
    assert "canary" not in serialized
    assert "secret_token" not in serialized


def test_system_rejects_secret_future_and_mixed_epoch_evidence(tmp_path: Path) -> None:
    # Given typed system evidence contains hostile fields or incompatible epochs
    root = tmp_path / "outputs/system"
    root.mkdir(parents=True)
    path = root / MILESTONE_FILE
    rows = [
        {
            "schema_version": 2,
            "evidence_type": "milestone",
            "epoch_id": epoch,
            "milestone_id": milestone,
            "status": "passed",
            "observed_at": observed_at,
            "code_sha256": "a" * 64,
            "result_code": result,
        }
        for epoch, milestone, observed_at, result in (
            ("release-1", "M0", NOW.isoformat(), "stage_passed"),
            ("release-2", "M1", "2099-01-01T00:00:00Z", "api_key_leaked"),
        )
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    path.chmod(0o600)

    # When the strict system reader parses it
    snapshot = collect_dashboard_snapshot_v2(tmp_path / "outputs", now=NOW)

    # Then the section fails closed without leaking hostile content
    assert snapshot.workspaces.system.state == "corrupt"
    assert "api_key" not in snapshot.model_dump_json().lower()


def _write_operations(
    root: Path,
    now: dt.datetime,
    *,
    mutation: str | None = None,
) -> None:
    rows = list(_operation_rows(now))
    if mutation == "launchd_stale":
        rows[0] = {
            **rows[0],
            "observed_at": (now - dt.timedelta(minutes=10)).isoformat(),
            "status": "running",
            "pid": 123,
            "process_started_at": (now - dt.timedelta(hours=1)).isoformat(),
        }
    elif mutation == "deployment_mismatch":
        rows[1] = {**rows[1], "expected_code_sha256": "f" * 64}
    elif mutation == "relay_stale":
        rows[2] = {**rows[2], "observed_at": (now - dt.timedelta(minutes=10)).isoformat()}
    path = root / OPERATIONS_FILE
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    path.chmod(0o600)


def _write_milestones(root: Path, now: dt.datetime) -> None:
    rows = (
        {
            "schema_version": 2,
            "evidence_type": "milestone",
            "epoch_id": "release-1",
            "milestone_id": milestone,
            "status": "passed",
            "observed_at": now.isoformat(),
            "code_sha256": f"{index + 1:064x}",
            "result_code": "stage_passed",
        }
        for index, milestone in enumerate(MILESTONE_IDS)
    )
    path = root / MILESTONE_FILE
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    path.chmod(0o600)


def _operation_rows(now: dt.datetime) -> tuple[dict[str, str | int | None], ...]:
    return (
        {
            "schema_version": 2,
            "evidence_type": "launchd",
            "evidence_id": "launchd.dashboard-publisher",
            "job_id": "dashboard-publisher",
            "observed_at": now.isoformat(),
            "status": "scheduled",
            "pid": None,
            "process_started_at": None,
            "last_exit_code": None,
            "receipt_sha256": "a" * 64,
        },
        {
            "schema_version": 2,
            "evidence_type": "railway",
            "evidence_id": "railway.dashboard",
            "deployment_id": "deploy-1",
            "observed_at": now.isoformat(),
            "code_sha256": "b" * 64,
            "expected_code_sha256": "b" * 64,
            "health": "healthy",
            "service_count": 1,
            "receipt_sha256": "c" * 64,
        },
        {
            "schema_version": 2,
            "evidence_type": "relay",
            "evidence_id": "relay.publisher",
            "transition_id": "transition-1",
            "observed_at": now.isoformat(),
            "state": "connected",
            "owner_sha256": "d" * 64,
            "receipt_sha256": "e" * 64,
        },
    )
