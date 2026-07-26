from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from tests.dashboard_paper_projection_fixtures import append_finalized_lifecycle
from trading_agent.dashboard_paper_finalized_terminal import (
    TERMINAL_FILENAME,
    FinalizedPaperTerminalReceipt,
)
from trading_agent.dashboard_projection_receipts import RECEIPT_FILENAME
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2

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
    assert all(workspace.state in {"blocked", "unavailable"} for _, workspace in snapshot.workspaces)
    assert all(workspace.blocker_code is not None for _, workspace in snapshot.workspaces)
    snapshot.model_validate_json(snapshot.model_dump_json())


def test_v2_snapshot_projects_finalized_paper_values_and_sha(tmp_path: Path) -> None:
    # Given an authoritative finalized lane ledger
    outputs = tmp_path / "outputs"
    append_finalized_lifecycle(outputs)

    # When it is projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then exact finalized values and immutable evidence SHA are emitted
    paper = snapshot.workspaces.paper
    assert paper.state == "populated"
    values = {item.item_id: item.value for item in paper.items}
    assert values["paper.daily_pnl"] == "104.75"
    assert values["paper.equity"] == "100125.25"
    assert values["paper.positions"] == "0 records"
    assert values["paper.orders"] == "0 records"
    assert values["paper.lifecycle.eod_flat"] == "finalized"
    receipt = FinalizedPaperTerminalReceipt.model_validate_json(
        (outputs / "paper" / TERMINAL_FILENAME).read_text().strip()
    )
    terminal_ref = hashlib.sha256(receipt.model_dump_json().encode()).hexdigest()
    assert any(node.safe_ref == terminal_ref and node.kind == "paper_receipt" for node in snapshot.traces.nodes)


@pytest.mark.parametrize("mutation", ["malformed", "permissions", "future", "mixed", "stale"])
def test_v2_generic_receipt_never_controls_research_state(tmp_path: Path, mutation: str) -> None:
    # Given one hostile, stale, or future generic research receipt
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

    # Then the authoritative experiment ledger remains the only source of truth
    research = snapshot.workspaces.research
    assert research.state == "unavailable"
    assert research.blocker_code == "research_catalog_missing"
    assert research.items == ()


def test_v2_receipts_are_bounded_and_reject_leakage(tmp_path: Path) -> None:
    # Given more than the per-workspace cap plus a secret-shaped canary
    outputs = tmp_path / "outputs"
    root = outputs / "experiment_control"
    root.mkdir(parents=True)
    rows = [
        {**_receipt(observed_at=NOW), "item_id": f"research.{index:03d}", "value": str(index)} for index in range(30)
    ]
    rows.append({**_receipt(observed_at=NOW), "item_id": "research.secret", "value": "api_key=canary"})
    path = root / RECEIPT_FILENAME
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    path.chmod(0o600)

    # When the source is projected
    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    # Then the generic source is ignored without leaking the canary
    research = snapshot.workspaces.research
    assert research.state == "unavailable"
    assert research.blocker_code == "research_catalog_missing"
    assert "canary" not in snapshot.model_dump_json().lower()


def test_v2_generic_receipts_cannot_claim_empty_or_populated_truth(tmp_path: Path) -> None:
    # Given generic receipts claiming accepted empty and populated authorities
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

    # Then neither generic claim becomes domain truth
    assert snapshot.workspaces.research.state == "unavailable"
    assert snapshot.workspaces.research.total_count == 0
    derivatives = snapshot.workspaces.derivatives
    assert derivatives.state == "unavailable"
    assert derivatives.projected_count == 0


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
def test_v2_generic_receipt_cannot_populate_authoritative_workspace(
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

    # Then the workspace remains unavailable and the strict DAG validates
    selected = dict(snapshot.workspaces)[workspace]
    assert selected.state in {"blocked", "unavailable"}
    assert all(item.value != "accepted" for item in selected.items)
    snapshot.model_validate_json(snapshot.model_dump_json())
    if workspace == "data_sources":
        assert all(item.entitlement == "unavailable" for item in snapshot.workspaces.data_sources.capabilities)


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
