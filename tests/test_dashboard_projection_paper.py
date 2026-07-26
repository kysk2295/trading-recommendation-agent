from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from trading_agent.dashboard_projection_paper import project_finalized_paper
from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_contract_keys import experiment_scope_key, lane_manifest_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import CURRENT_INTRADAY_EXPERIMENT_SCOPES, INTRADAY_MANIFEST
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.paper_execution_models import AccountFingerprint

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)


def test_finalized_zero_positions_and_orders_are_valid_empty_sections(tmp_path: Path) -> None:
    # Given a complete finalized append-only Paper daily ledger
    outputs = tmp_path / "outputs"
    _append_snapshot(outputs, complete=True, open_orders=0, open_positions=0)

    # When the read-only Paper projection is built
    projection = project_finalized_paper(outputs, now=NOW)

    # Then finalized values remain verified while zero collections are explicit empty states
    items = {item.item_id: item for item in projection.workspace.items}
    assert projection.workspace.state == "populated"
    assert items["paper.positions"].state == "empty"
    assert items["paper.positions"].value == "0 records"
    assert items["paper.orders"].state == "empty"
    assert items["paper.orders"].value == "0 records"
    assert items["paper.lifecycle.eod_flat"].value == "finalized"
    assert all(node.kind == "paper_receipt" for node in projection.nodes if node.node_id.endswith("finalized"))


def test_incomplete_paper_verification_never_projects_values(tmp_path: Path) -> None:
    # Given an append-only daily snapshot whose verification is incomplete
    outputs = tmp_path / "outputs"
    _append_snapshot(outputs, complete=False, open_orders=0, open_positions=0)

    # When the Paper workspace is projected
    projection = project_finalized_paper(outputs, now=NOW)

    # Then it fails closed without live or verified account values
    assert projection.workspace.state == "blocked"
    assert projection.workspace.blocker_code == "paper_verification_incomplete"
    assert projection.workspace.items == ()
    assert "verified" not in projection.workspace.model_dump_json().lower()


def test_paper_lifecycle_uses_exact_finalized_execution_generation(tmp_path: Path) -> None:
    # Given an initialized append-only execution ledger sealed into the daily snapshot
    outputs = tmp_path / "outputs"
    execution_path = outputs / "paper" / "execution.sqlite3"
    store = ExecutionStore(execution_path)
    with store.writer() as writer:
        assert writer.bind_account(
            AccountFingerprint("b" * 64),
            dt.datetime(2026, 7, 25, 13, 30, tzinfo=dt.UTC),
        )
    identity = store.ledger_snapshot_identity()
    _append_snapshot(
        outputs,
        complete=True,
        open_orders=0,
        open_positions=0,
        source_generation=identity.generation,
        source_sha256=identity.sha256,
    )

    # When the finalized Paper projection reads both ledgers
    projection = project_finalized_paper(outputs, now=NOW)

    # Then entry and OCO collections are exact valid-empty lifecycle evidence
    items = {item.item_id: item for item in projection.workspace.items}
    assert items["paper.lifecycle.entry"].value == "0 records"
    assert items["paper.lifecycle.entry"].state == "empty"
    assert items["paper.lifecycle.protective_oco"].value == "0 records"
    assert items["paper.lifecycle.protective_oco"].state == "empty"


def _append_snapshot(
    outputs: Path,
    *,
    complete: bool,
    open_orders: int,
    open_positions: int,
    source_generation: int = 42,
    source_sha256: str = "a" * 64,
) -> None:
    registry = LaneRegistryStore(outputs / "lane_control" / "lane_registry.sqlite3")
    scope = CURRENT_INTRADAY_EXPERIMENT_SCOPES[0]
    snapshot = LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=dt.date(2026, 7, 25),
        finalized_at=dt.datetime(2026, 7, 25, 20, 5, tzinfo=dt.UTC),
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(experiment_scope_key(scope),),
        source_ledger_generation=source_generation,
        source_ledger_sha256=source_sha256,
        champion_strategy_versions=(),
        data_quality_complete=complete,
        allocation_eligible=False,
        incidents=(),
        conservative_equity=Decimal("100125.25"),
        realized_pnl=Decimal("125.25"),
        unrealized_pnl=Decimal("-20.50"),
        planned_open_risk=Decimal("0"),
        open_order_count=open_orders,
        open_position_count=open_positions,
    )
    with registry.writer() as writer:
        _ = writer.register_manifest(INTRADAY_MANIFEST)
        _ = writer.register_experiment_scope(scope)
        assert writer.append_daily_snapshot(snapshot)
