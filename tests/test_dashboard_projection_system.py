from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from dashboard_system_fixtures import (
    NOW,
    JsonRow,
    control_receipts,
    milestones,
    operations,
    system_root,
    write_rows,
)

from trading_agent.dashboard_projection_system import project_system
from trading_agent.dashboard_system_control_receipts import AUTONOMOUS_CONTROL_FILE
from trading_agent.dashboard_system_evidence import MILESTONE_FILE, MILESTONE_IDS
from trading_agent.dashboard_system_operations import OPERATIONS_FILE


def test_complete_system_projection_keeps_exact_milestones_and_typed_operations(
    tmp_path: Path,
) -> None:
    root = system_root(tmp_path)
    write_rows(root / MILESTONE_FILE, milestones())
    write_rows(root / OPERATIONS_FILE, operations())
    write_rows(root / AUTONOMOUS_CONTROL_FILE, control_receipts())

    projection = project_system(tmp_path / "outputs", now=NOW)

    assert projection.workspace.state == "populated"
    assert tuple(item.label for item in projection.workspace.items[:11]) == MILESTONE_IDS
    assert {item.label for item in projection.workspace.items[11:]} >= {
        "dashboard-publisher",
        "Release verification",
        "Railway deployment",
        "Event relay",
        "Autonomous scheduler",
        "Autonomous trigger",
        "Autonomous claim",
        "Budget gate",
        "Cleanup receipt",
    }
    assert all(item.observed_at is not None for item in projection.workspace.items)
    assert all(
        any(
            edge.from_node_id == item.trace_id
            and next(
                node for node in projection.nodes if node.node_id == edge.to_node_id
            ).kind
            in {"process_receipt", "deployment_receipt", "blocker_terminal"}
            for edge in projection.edges
        )
        for item in projection.workspace.items
    )


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ("stale_pid", "launchd_pid_stale"),
        ("nonzero_exit", "launchd_job_failed"),
        ("unverified_exit", "launchd_exit_unverified"),
        ("stage_failed", "stage_failed"),
        ("stage_terminal_missing", "stage_terminal_missing"),
        ("railway_unreachable", "railway_health_failed"),
        ("relay_stale", "relay_receipt_stale"),
        ("cleanup_failed", "autonomous_cleanup_failed"),
        ("budget_blocked", "family_token_budget_exhausted"),
    ],
)
def test_adverse_system_receipts_fail_closed(
    tmp_path: Path,
    mutation: str,
    blocker: str,
) -> None:
    root = system_root(tmp_path)
    write_rows(root / MILESTONE_FILE, milestones())
    write_rows(root / OPERATIONS_FILE, operations(mutation))
    write_rows(root / AUTONOMOUS_CONTROL_FILE, control_receipts(mutation))

    projection = project_system(tmp_path / "outputs", now=NOW)

    assert projection.workspace.state in {"blocked", "stale", "unavailable"}
    assert projection.workspace.blocker_code == blocker
    assert any(
        node.kind == "blocker_terminal" and node.state == "blocked"
        for node in projection.nodes
    )


@pytest.mark.parametrize(
    "hostile",
    [
        {"agent_family_id": "day_trading"},
        {"job_id": "day_trading"},
        {"raw_log": "success"},
        {"worktree_id": "private-worktree"},
        {"session_id": "private-session"},
        {"api_key": "private-secret"},
        {"environment": {"TOKEN": "secret"}},
        {"path": "/Users/canary/private"},
    ],
)
def test_launchd_alias_and_private_fields_never_project(
    tmp_path: Path,
    hostile: dict[str, str | dict[str, str]],
) -> None:
    root = system_root(tmp_path)
    write_rows(root / MILESTONE_FILE, milestones())
    row: JsonRow = {**operations()[0], **hostile}
    write_rows(root / OPERATIONS_FILE, (row, *operations()[1:]))
    write_rows(root / AUTONOMOUS_CONTROL_FILE, control_receipts())

    projection = project_system(tmp_path / "outputs", now=NOW)
    payload = projection.workspace.model_dump_json().lower()

    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code in {
        "system_operations_invalid",
        "system_operations_forbidden_content",
    }
    assert "canary" not in payload
    assert "private-worktree" not in payload
    assert "secret" not in payload


@pytest.mark.parametrize("source", ["milestones", "operations", "autonomous"])
def test_missing_authority_is_unavailable(tmp_path: Path, source: str) -> None:
    root = system_root(tmp_path)
    if source != "milestones":
        write_rows(root / MILESTONE_FILE, milestones())
    if source != "operations":
        write_rows(root / OPERATIONS_FILE, operations())
    if source != "autonomous":
        write_rows(root / AUTONOMOUS_CONTROL_FILE, control_receipts())

    projection = project_system(tmp_path / "outputs", now=NOW)

    assert projection.workspace.state == "unavailable"
    assert projection.workspace.blocker_code is not None


def test_future_corrupt_and_oversized_receipts_are_bounded(tmp_path: Path) -> None:
    root = system_root(tmp_path)
    write_rows(root / MILESTONE_FILE, milestones())
    rows = tuple(
        {
            **control_receipts()[0],
            "evidence_id": f"control-{index}",
            "observed_at": (
                NOW + dt.timedelta(hours=1) if index == 30 else NOW
            ).isoformat(),
        }
        for index in range(31)
    )
    write_rows(root / OPERATIONS_FILE, operations())
    write_rows(root / AUTONOMOUS_CONTROL_FILE, rows)

    projection = project_system(tmp_path / "outputs", now=NOW)

    assert projection.workspace.state == "corrupt"
    assert projection.workspace.projected_count <= 24
    assert len(projection.workspace.items) <= 24
