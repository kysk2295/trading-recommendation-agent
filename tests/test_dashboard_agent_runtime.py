from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.dashboard_agent_runtime import (
    append_agent_runtime_readiness,
)
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2

NOW = dt.datetime(2026, 7, 27, 5, 30, tzinfo=dt.UTC)


def test_exact_six_agents_become_armed_from_three_channel_runtime_receipts(
    tmp_path: Path,
) -> None:
    outputs = tmp_path / "outputs"
    paths = append_agent_runtime_readiness(
        outputs,
        observed_at=NOW,
        code_sha256="a" * 40,
        state="armed",
    )

    snapshot = collect_dashboard_snapshot_v2(outputs, now=NOW)

    assert len(paths) == 18
    assert snapshot.workspaces.command_center.state == "populated"
    assert tuple(
        agent.runtime_state
        for agent in snapshot.workspaces.command_center.agents
    ) == ("armed",) * 6
    assert all(
        agent.trace_id != snapshot.workspaces.command_center.trace_id
        for agent in snapshot.workspaces.command_center.agents
    )
