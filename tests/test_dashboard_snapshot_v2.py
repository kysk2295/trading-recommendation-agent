from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.dashboard_projection_receipts import RECEIPT_FILENAME
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.lane_contract_keys import experiment_scope_key, lane_manifest_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import CURRENT_INTRADAY_EXPERIMENT_SCOPES, INTRADAY_MANIFEST
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryStore

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)


def test_v2_snapshot_reports_all_missing_authorities_section_locally(tmp_path: Path) -> None:
    # Given an empty output root
    outputs = tmp_path / "outputs"

    # When the canonical projector reads it
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then all nine workspaces fail closed with resolvable blocker traces
    assert snapshot.schema_version == 2
    assert {name for name, _ in snapshot.workspaces} == {
        "command_center",
        "overview",
        "markets",
        "data_sources",
        "research",
        "strategies",
        "derivatives",
        "paper",
        "system",
    }
    assert all(workspace.state == "unavailable" for _, workspace in snapshot.workspaces)
    assert len({workspace.blocker_code for _, workspace in snapshot.workspaces}) == 9
    snapshot.model_validate_json(snapshot.model_dump_json())


def test_v2_snapshot_projects_finalized_paper_values_and_sha(tmp_path: Path) -> None:
    # Given an authoritative finalized lane ledger
    outputs = tmp_path / "outputs"
    registry = LaneRegistryStore(outputs / "lane_control" / "lane_registry.sqlite3")
    scope = CURRENT_INTRADAY_EXPERIMENT_SCOPES[0]
    daily = LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=dt.date(2026, 7, 25),
        finalized_at=dt.datetime(2026, 7, 25, 20, 5, tzinfo=dt.UTC),
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(experiment_scope_key(scope),),
        source_ledger_generation=42,
        source_ledger_sha256="a" * 64,
        champion_strategy_versions=(),
        data_quality_complete=True,
        allocation_eligible=False,
        incidents=(),
        conservative_equity=Decimal("100125.25"),
        realized_pnl=Decimal("125.25"),
        unrealized_pnl=Decimal("-20.50"),
        planned_open_risk=Decimal("0"),
        open_order_count=0,
        open_position_count=0,
    )
    with registry.writer() as writer:
        _ = writer.register_manifest(INTRADAY_MANIFEST)
        _ = writer.register_experiment_scope(scope)
        assert writer.append_daily_snapshot(daily)

    # When it is projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then exact finalized values and immutable evidence SHA are emitted
    paper = snapshot.workspaces.paper
    assert paper.state == "populated"
    assert {item.item_id: item.value for item in paper.items} == {
        "paper.daily_pnl": "104.75",
        "paper.equity": "100125.25",
        "paper.open_orders": "0",
        "paper.open_positions": "0",
    }
    assert any(node.safe_ref == "a" * 64 and node.kind == "paper_receipt" for node in snapshot.traces.nodes)


@pytest.mark.parametrize(
    ("mutation", "state", "blocker"),
    [
        ("malformed", "corrupt", "research_receipt_invalid"),
        ("permissions", "corrupt", "research_source_permissions_invalid"),
        ("future", "corrupt", "research_future_observation"),
        ("mixed", "corrupt", "research_mixed_snapshot_epoch"),
        ("stale", "stale", None),
    ],
)
def test_v2_receipt_reader_distinguishes_hostile_states(
    tmp_path: Path,
    mutation: str,
    state: str,
    blocker: str | None,
) -> None:
    # Given one hostile or stale append-only research receipt
    outputs = tmp_path / "outputs"
    root = outputs / "experiment_control"
    root.mkdir(parents=True)
    path = root / RECEIPT_FILENAME
    receipt = _receipt(observed_at=NOW)
    if mutation == "malformed":
        path.write_text("{", encoding="utf-8")
        path.chmod(0o600)
    else:
        if mutation == "future":
            receipt["observed_at"] = "2099-01-01T00:00:00Z"
            path.write_text(json.dumps(receipt), encoding="utf-8")
        elif mutation == "mixed":
            second = {**receipt, "snapshot_epoch": "epoch-2", "item_id": "research.b"}
            path.write_text("\n".join((json.dumps(receipt), json.dumps(second))), encoding="utf-8")
        elif mutation == "stale":
            receipt["observed_at"] = "2026-07-01T00:00:00Z"
            path.write_text(json.dumps(receipt), encoding="utf-8")
        else:
            path.write_text(json.dumps(receipt), encoding="utf-8")
        path.chmod(0o644 if mutation == "permissions" else 0o600)

    # When the source is projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then only research reports the exact canonical state
    research = snapshot.workspaces.research
    assert research.state == state
    assert research.blocker_code == blocker
    assert snapshot.workspaces.strategies.blocker_code == "strategies_authority_missing"


def test_v2_receipts_are_bounded_and_reject_leakage(tmp_path: Path) -> None:
    # Given more than the per-workspace cap plus a secret-shaped canary
    outputs = tmp_path / "outputs"
    root = outputs / "experiment_control"
    root.mkdir(parents=True)
    rows = [
        {**_receipt(observed_at=NOW), "item_id": f"research.{index:03d}", "value": str(index)}
        for index in range(30)
    ]
    rows.append({**_receipt(observed_at=NOW), "item_id": "research.secret", "value": "api_key=canary"})
    path = root / RECEIPT_FILENAME
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    path.chmod(0o600)

    # When the source is projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then the hostile source fails closed without leaking the canary
    research = snapshot.workspaces.research
    assert research.state == "corrupt"
    assert research.blocker_code == "research_forbidden_content"
    assert "canary" not in snapshot.model_dump_json().lower()


def test_v2_receipts_preserve_valid_empty_and_cap_populated_rows(tmp_path: Path) -> None:
    # Given accepted empty and oversized populated authorities
    outputs = tmp_path / "outputs"
    research_root = outputs / "experiment_control"
    derivative_root = outputs / "derivatives"
    research_root.mkdir(parents=True)
    derivative_root.mkdir(parents=True)
    empty = {**_receipt(observed_at=NOW), "state": "empty", "value": None}
    research_path = research_root / RECEIPT_FILENAME
    research_path.write_text(json.dumps(empty), encoding="utf-8")
    research_path.chmod(0o600)
    rows = [
        {
            **_receipt(observed_at=NOW),
            "workspace": "derivatives",
            "kind": "derivative",
            "item_id": f"derivative.{index:03d}",
            "terminal_kind": "source_receipt",
        }
        for index in range(30)
    ]
    derivative_path = derivative_root / RECEIPT_FILENAME
    derivative_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    derivative_path.chmod(0o600)

    # When they are projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then empty remains successful and populated rows carry exact cap metadata
    assert snapshot.workspaces.research.state == "empty"
    assert snapshot.workspaces.research.total_count == 0
    derivatives = snapshot.workspaces.derivatives
    assert (derivatives.total_count, derivatives.projected_count, derivatives.truncated) == (30, 24, True)


@pytest.mark.parametrize(
    ("workspace", "root_name", "terminal"),
    [
        ("command_center", "system", "process_receipt"),
        ("overview", "live_sessions", "source_receipt"),
        ("markets", "live_sessions", "source_receipt"),
        ("data_sources", "source_evidence", "source_receipt"),
        ("research", "experiment_control", "reviewer_decision"),
        ("strategies", "lane_control", "lifecycle_decision"),
        ("derivatives", "derivatives", "reviewer_decision"),
        ("paper", "paper", "paper_receipt"),
        ("system", "system", "deployment_receipt"),
    ],
)
def test_v2_receipt_populates_each_workspace_with_resolvable_trace(
    tmp_path: Path,
    workspace: str,
    root_name: str,
    terminal: str,
) -> None:
    # Given a domain-appropriate accepted receipt
    outputs = tmp_path / "outputs"
    root = outputs / root_name
    root.mkdir(parents=True)
    receipt = {
        **_receipt(observed_at=NOW),
        "workspace": workspace,
        "kind": _kind_for(workspace),
        "item_id": f"{workspace}.accepted",
        "terminal_kind": terminal,
    }
    path = root / RECEIPT_FILENAME
    path.write_text(json.dumps(receipt), encoding="utf-8")
    path.chmod(0o600)

    # When the canonical projector reads it
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then the workspace is populated and the strict DAG validates
    selected = dict(snapshot.workspaces)[workspace]
    assert selected.state == "populated"
    assert selected.items[0].value == "accepted"
    snapshot.model_validate_json(snapshot.model_dump_json())
    if workspace == "data_sources":
        assert all(item.entitlement == "research_only" for item in snapshot.workspaces.data_sources.capabilities)


def _receipt(*, observed_at: dt.datetime) -> dict[str, str | int]:
    return {
        "schema_version": 2,
        "snapshot_epoch": "epoch-1",
        "workspace": "research",
        "item_id": "research.a",
        "kind": "research",
        "label": "Dataset",
        "value": "accepted",
        "observed_at": observed_at.isoformat(),
        "safe_ref": "b" * 64,
        "terminal_kind": "reviewer_decision",
        "state": "populated",
    }


def _kind_for(workspace: str) -> str:
    return {
        "command_center": "system",
        "overview": "metric",
        "markets": "metric",
        "data_sources": "metric",
        "research": "research",
        "strategies": "strategy",
        "derivatives": "derivative",
        "paper": "paper",
        "system": "system",
    }[workspace]
