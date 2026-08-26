from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from tests.test_research_agent_runtime import (
    EMPTY_COLLECTOR,
    NOW,
    MarketIsolatedDayActionClient,
    RecordingArtifactActionClient,
    RecordingDecisionClient,
    RecordingSupervisor,
    _evidence,
    _runtime,
)
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1, ResearchAgentOpenWorkState
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_runtime import ResearchAgentRuntime, ResearchAgentRuntimeServices


def _supervisor_runtime(path: Path, delegated: list[ResearchAgentEvidenceV1]) -> ResearchAgentRuntime:
    return ResearchAgentRuntime(
        ResearchAgentRuntimeServices(
            ResearchAgentCycleStore(path),
            EMPTY_COLLECTOR,
            RecordingDecisionClient([]),
            RecordingArtifactActionClient([]),
            supervisor_runtime=RecordingSupervisor(delegated),
        )
    )


def test_cutover_failure_rolls_back_cycle_result_and_legacy_work_together(tmp_path: Path) -> None:
    # Given: legacy retry work and an injected failure on its terminal update.
    path = tmp_path / "cycles.sqlite3"
    original = _evidence("day_trading", 1, "us_equities")
    legacy = _runtime(path, EMPTY_COLLECTOR, [], MarketIsolatedDayActionClient([]))
    legacy.ingest((original,))
    assert legacy.tick(NOW + dt.timedelta(minutes=1)).status == "failed"
    legacy.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER injected_cutover_failure BEFORE UPDATE ON open_work "
            "BEGIN SELECT RAISE(ABORT, 'injected-cutover-failure'); END"
        )
    delegated: list[ResearchAgentEvidenceV1] = []
    cutover = _supervisor_runtime(path, delegated)

    # When: supervisor persistence reaches the failing open-work boundary.
    with pytest.raises(sqlite3.IntegrityError, match="injected-cutover-failure"):
        cutover.tick(NOW + dt.timedelta(minutes=16))
    cutover.close()

    # Then: restart sees no committed half-result and completes one recovered cycle.
    with ResearchAgentCycleStore(path) as inspection:
        assert len(inspection.results()) == 1
        assert inspection.open_work("day_trading")[0].state is ResearchAgentOpenWorkState.OPEN
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cycles WHERE state='started'").fetchone() == (1,)
        connection.execute("DROP TRIGGER injected_cutover_failure")
    restarted = _supervisor_runtime(path, delegated)
    result = restarted.tick(NOW + dt.timedelta(minutes=17))
    assert result.cycle_id is not None
    events = restarted.store.cycle_events(result.cycle_id)
    results = restarted.store.results()
    work = restarted.store.open_work("day_trading")
    restarted.close()
    assert result.status == "no_action"
    assert len(results) == 2
    assert tuple(event.state for event in events) == ("started", "interrupted", "started", "completed")
    assert work[0].state is ResearchAgentOpenWorkState.TERMINAL
    assert delegated == [original, original]
