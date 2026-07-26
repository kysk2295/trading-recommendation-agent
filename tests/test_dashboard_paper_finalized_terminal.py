from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from tests.dashboard_paper_projection_fixtures import (
    FINALIZED_AT,
    append_swing_snapshot,
)
from trading_agent.dashboard_paper_finalized_terminal import TERMINAL_FILENAME
from trading_agent.dashboard_projection_paper import project_finalized_paper

NOW = dt.datetime(2026, 7, 26, 3, tzinfo=dt.UTC)


def test_finalized_swing_counts_require_execution_ledger(tmp_path: Path) -> None:
    # Given a valid swing snapshot and terminal whose bound execution DB is absent
    outputs = tmp_path / "outputs"
    append_swing_snapshot(outputs, open_positions=2, open_orders=3)
    (outputs / "paper" / "execution.sqlite3").unlink()

    # When the Paper projection resolves finalized authority
    projection = project_finalized_paper(outputs, now=NOW)

    # Then it is unavailable and never publishes snapshot-declared counts
    assert projection.workspace.state == "unavailable"
    assert projection.workspace.blocker_code == "paper_finalized_execution_missing"
    assert projection.workspace.items == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_ledger_generation", 999),
        ("source_ledger_sha256", "d" * 64),
        ("lane_id", "intraday_momentum"),
        ("session_date", "2026-07-24"),
    ),
)
def test_finalized_terminal_requires_exact_generation_lane_and_session(
    tmp_path: Path,
    field: str,
    value: str | int,
) -> None:
    # Given one immutable terminal binding field differs from the stored swing snapshot
    outputs = tmp_path / "outputs"
    append_swing_snapshot(outputs, open_positions=2, open_orders=3)
    _rewrite_terminal(outputs, {field: value})

    # When the Paper projection authenticates the terminal
    projection = project_finalized_paper(outputs, now=NOW)

    # Then it fails closed without synthesizing a Paper receipt
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "paper_finalized_terminal_invalid"
    assert projection.workspace.items == ()


def test_exact_finalized_terminal_can_age_to_stale(tmp_path: Path) -> None:
    # Given one exact terminal older than the finalized-session freshness policy
    outputs = tmp_path / "outputs"
    append_swing_snapshot(outputs, open_positions=2, open_orders=3)

    # When the exact authority is projected four days later
    projection = project_finalized_paper(
        outputs,
        now=FINALIZED_AT + dt.timedelta(days=4),
    )

    # Then truthful counts remain visible but explicitly stale
    assert projection.workspace.state == "stale"
    items = {item.item_id: item for item in projection.workspace.items}
    assert items["paper.positions"].state == "stale"
    assert items["paper.orders"].state == "stale"


def test_future_finalized_terminal_fails_closed(tmp_path: Path) -> None:
    # Given a terminal observation later than its exact finalized snapshot
    outputs = tmp_path / "outputs"
    append_swing_snapshot(outputs, open_positions=2, open_orders=3)
    _rewrite_terminal(
        outputs,
        {"observed_at": (FINALIZED_AT + dt.timedelta(minutes=1)).isoformat()},
    )

    # When the future terminal is authenticated
    projection = project_finalized_paper(outputs, now=NOW)

    # Then it is corrupt and emits no receipt-backed values
    assert projection.workspace.state == "corrupt"
    assert projection.workspace.blocker_code == "paper_finalized_terminal_invalid"
    assert projection.workspace.items == ()


def _rewrite_terminal(
    outputs: Path,
    updates: dict[str, str | int],
) -> None:
    path = outputs / "paper" / TERMINAL_FILENAME
    payload = json.loads(path.read_text())
    payload.update(updates)
    path.write_text(f"{json.dumps(payload)}\n")
    path.chmod(0o600)
