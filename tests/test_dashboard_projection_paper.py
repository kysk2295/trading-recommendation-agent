from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

import pytest

from tests.dashboard_paper_projection_fixtures import (
    LifecycleFixture,
    MissingStage,
    append_daily_snapshot,
    append_finalized_lifecycle,
    safety_plan,
)
from trading_agent.dashboard_projection_paper import project_finalized_paper
from trading_agent.paper_safety_models import PaperSafetyPhase

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)


def test_finalized_zero_positions_and_orders_are_valid_empty_sections(tmp_path: Path) -> None:
    # Given a complete finalized append-only Paper daily ledger
    outputs = tmp_path / "outputs"
    append_finalized_lifecycle(outputs)

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
    append_daily_snapshot(outputs, complete=False)

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
    append_finalized_lifecycle(outputs)

    # When the finalized Paper projection reads both ledgers
    projection = project_finalized_paper(outputs, now=NOW)

    # Then entry and OCO collections are exact valid-empty lifecycle evidence
    items = {item.item_id: item for item in projection.workspace.items}
    assert items["paper.lifecycle.entry"].value == "0 records"
    assert items["paper.lifecycle.entry"].state == "empty"
    assert items["paper.lifecycle.protective_oco"].value == "0 records"
    assert items["paper.lifecycle.protective_oco"].state == "empty"


@pytest.mark.parametrize(
    ("missing", "blocker_code"),
    (
        ("reconcile", "paper_reconcile_pending"),
        ("cutoff", "paper_cutoff_pending"),
        ("eod_flat", "paper_eod_flat_pending"),
    ),
)
def test_paper_lifecycle_requires_each_finalized_stage_receipt(
    tmp_path: Path,
    missing: MissingStage,
    blocker_code: str,
) -> None:
    # Given an exact finalized execution generation missing one lifecycle stage receipt
    outputs = tmp_path / "outputs"
    append_finalized_lifecycle(outputs, LifecycleFixture(missing=missing))

    # When the Paper workspace projects the finalized lifecycle
    projection = project_finalized_paper(outputs, now=NOW)

    # Then the missing stage blocks with its precise reason and emits no finalized value
    assert projection.workspace.state == "blocked"
    assert projection.workspace.blocker_code == blocker_code
    assert projection.workspace.items == ()


@pytest.mark.parametrize(
    ("fixture", "state", "blocker_code"),
    (
        (
            LifecycleFixture(cutoff_at=dt.datetime(2026, 7, 25, 19, 35, tzinfo=dt.UTC)),
            "corrupt",
            "paper_lifecycle_invalid",
        ),
        (
            LifecycleFixture(eod_at=dt.datetime(2026, 7, 25, 20, 10, tzinfo=dt.UTC)),
            "corrupt",
            "paper_lifecycle_invalid",
        ),
        (
            LifecycleFixture(
                eod_at=dt.datetime(2026, 7, 24, 19, 50, tzinfo=dt.UTC),
                eod_session_date=dt.date(2026, 7, 24),
            ),
            "blocked",
            "paper_eod_flat_pending",
        ),
    ),
)
def test_paper_lifecycle_rejects_out_of_order_future_or_disconnected_stage(
    tmp_path: Path,
    fixture: LifecycleFixture,
    state: Literal["blocked", "corrupt"],
    blocker_code: str,
) -> None:
    # Given stage receipts that cannot form one ordered finalized lifecycle
    outputs = tmp_path / "outputs"
    append_finalized_lifecycle(outputs, fixture)

    # When the Paper workspace joins the stage receipts
    projection = project_finalized_paper(outputs, now=NOW)

    # Then the lifecycle fails closed without a synthesized final state
    assert projection.workspace.state == state
    assert projection.workspace.blocker_code == blocker_code
    assert projection.workspace.items == ()


def test_paper_lifecycle_rejects_post_snapshot_generation(tmp_path: Path) -> None:
    # Given a complete lifecycle followed by a receipt outside the finalized ledger identity
    outputs = tmp_path / "outputs"
    store = append_finalized_lifecycle(outputs)
    with store.writer() as writer:
        _ = writer.save_paper_safety_plan(
            safety_plan(
                PaperSafetyPhase.KILL_SWITCH,
                dt.datetime(2026, 7, 25, 19, 55, tzinfo=dt.UTC),
            )
        )

    # When the Paper workspace compares the current ledger with the finalized generation
    projection = project_finalized_paper(outputs, now=NOW)

    # Then the mixed generation is rejected before any finalized stage is shown
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "paper_epoch_mismatch"
    assert projection.workspace.items == ()
