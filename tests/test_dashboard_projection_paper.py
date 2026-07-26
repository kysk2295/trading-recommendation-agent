from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from tests.dashboard_paper_projection_fixtures import (
    LifecycleFixture,
    MissingStage,
    append_daily_snapshot,
    append_finalized_lifecycle,
    append_swing_snapshot,
    safety_plan,
)
from trading_agent.dashboard_projection_paper import project_finalized_paper
from trading_agent.lane_contract_keys import lane_daily_snapshot_key
from trading_agent.lane_contract_models import LaneDailySnapshot
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


def test_finalized_nonzero_positions_and_orders_preserve_authoritative_counts(
    tmp_path: Path,
) -> None:
    # Given a valid serialized swing snapshot whose contract permits carried exposure
    outputs = tmp_path / "outputs"
    append_swing_snapshot(outputs, open_positions=2, open_orders=3)

    # When the read-only Paper projection renders finalized counts
    projection = project_finalized_paper(outputs, now=NOW)

    # Then it displays the exact nonzero values instead of fabricating zero
    items = {item.item_id: item for item in projection.workspace.items}
    assert items["paper.positions"].state == "populated"
    assert items["paper.positions"].value == "2 records"
    assert items["paper.orders"].state == "populated"
    assert items["paper.orders"].value == "3 records"
    assert projection.workspace.state == "populated"


def test_rekeyed_arbitrary_snapshot_generation_never_replaces_execution_identity(
    tmp_path: Path,
) -> None:
    # Given a valid swing snapshot rekeyed around an arbitrary ledger generation
    outputs = tmp_path / "outputs"
    append_swing_snapshot(outputs, open_positions=2, open_orders=3)
    _rewrite_finalized_snapshot(
        outputs,
        updates={"source_ledger_generation": 999},
        rekey=True,
    )

    # When lifecycle generation is checked against the actual execution DB
    projection = project_finalized_paper(outputs, now=NOW)

    # Then the mixed epoch fails before any finalized value is shown
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "paper_epoch_mismatch"
    assert projection.workspace.items == ()


def test_finalized_count_payload_must_match_immutable_snapshot_key(
    tmp_path: Path,
) -> None:
    # Given a valid stored swing count changed without changing its immutable key
    outputs = tmp_path / "outputs"
    append_swing_snapshot(outputs, open_positions=2, open_orders=3)
    _rewrite_finalized_snapshot(
        outputs,
        updates={"open_position_count": 4},
    )

    # When the Paper projection verifies the immutable count authority
    projection = project_finalized_paper(outputs, now=NOW)

    # Then the mismatch fails closed and emits no fabricated counts
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "paper_finalized_ledger_invalid"
    assert projection.workspace.items == ()


def test_intraday_nonzero_finalized_count_remains_invalid(tmp_path: Path) -> None:
    # Given an intraday finalized row tampered to retain a position after EOD-flat
    outputs = tmp_path / "outputs"
    append_finalized_lifecycle(outputs)
    _rewrite_finalized_snapshot(
        outputs,
        updates={"open_position_count": 1},
    )

    # When production validation reads the invalid intraday snapshot
    projection = project_finalized_paper(outputs, now=NOW)

    # Then the flat-by-close contract rejects it before count presentation
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "paper_finalized_ledger_invalid"
    assert projection.workspace.items == ()


def test_same_session_paper_authorities_never_mix_lanes(tmp_path: Path) -> None:
    # Given valid intraday and swing finalized snapshots for the same session
    outputs = tmp_path / "outputs"
    append_finalized_lifecycle(outputs)
    append_swing_snapshot(outputs, open_positions=2, open_orders=3)

    # When the Paper projection selects one finalized authority
    projection = project_finalized_paper(outputs, now=NOW)

    # Then the ambiguous cross-lane authority fails closed without mixed counts
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "paper_finalized_ledger_invalid"
    assert projection.workspace.items == ()


def test_finalized_snapshot_missing_count_fails_closed(tmp_path: Path) -> None:
    # Given a persisted finalized snapshot whose position count is missing
    outputs = tmp_path / "outputs"
    append_finalized_lifecycle(outputs)
    _rewrite_finalized_snapshot(outputs, missing="open_position_count")

    # When the Paper projection reads the malformed authority
    projection = project_finalized_paper(outputs, now=NOW)

    # Then validation fails closed before any count is emitted
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "paper_finalized_ledger_invalid"
    assert projection.workspace.items == ()


def _rewrite_finalized_snapshot(
    outputs: Path,
    *,
    updates: dict[str, int] | None = None,
    missing: str | None = None,
    rekey: bool = False,
) -> None:
    path = outputs / "lane_control" / "lane_registry.sqlite3"
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM lane_daily_snapshots").fetchone()[0])
        payload.update(updates or {})
        if missing is not None:
            del payload[missing]
        _ = connection.execute("DROP TRIGGER lane_daily_snapshots_no_update")
        snapshot_key = connection.execute("SELECT snapshot_key FROM lane_daily_snapshots").fetchone()[0]
        if rekey:
            snapshot_key = str(lane_daily_snapshot_key(LaneDailySnapshot.model_validate(payload)))
        _ = connection.execute(
            "UPDATE lane_daily_snapshots SET snapshot_key = ?, payload_json = ?",
            (snapshot_key, json.dumps(payload)),
        )
        connection.commit()
        _ = connection.execute("PRAGMA journal_mode = DELETE").fetchone()


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
