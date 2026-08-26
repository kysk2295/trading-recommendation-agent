from __future__ import annotations

import datetime as dt
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
from trading_agent.autonomous_supervisor_cycle_adapter import InvalidSupervisorCycleResolutionError
from trading_agent.research_agent_cycle_models import (
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkState,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_runtime import ResearchAgentRuntime, ResearchAgentRuntimeServices


def test_supervisor_cutover_resumes_original_open_work_evidence(tmp_path: Path) -> None:
    # Given: a legacy US Day failure left retryable work bound to original evidence.
    path = tmp_path / "cycles.sqlite3"
    original = _evidence("day_trading", 1, "us_equities")
    legacy = _runtime(path, EMPTY_COLLECTOR, [], MarketIsolatedDayActionClient([]))
    legacy.ingest((original,))
    assert legacy.tick(NOW + dt.timedelta(minutes=1)).status == "failed"
    legacy.close()
    delegated: list[ResearchAgentEvidenceV1] = []
    restarted = ResearchAgentRuntime(
        ResearchAgentRuntimeServices(
            ResearchAgentCycleStore(path),
            EMPTY_COLLECTOR,
            RecordingDecisionClient([]),
            RecordingArtifactActionClient([]),
            supervisor_runtime=RecordingSupervisor(delegated),
        )
    )

    # When: the installed supervisor resumes the due open work.
    result = restarted.tick(NOW + dt.timedelta(minutes=16))
    legacy_work = restarted.store.open_work("day_trading")
    restarted.close()

    # Then: it receives original authority evidence rather than a retry envelope.
    assert result.status == "no_action"
    assert delegated[0].evidence_id == original.evidence_id
    assert delegated[0].source_key == original.source_key
    assert legacy_work[0].state is ResearchAgentOpenWorkState.TERMINAL


def test_supervisor_cutover_rejects_cross_market_day_evidence(tmp_path: Path) -> None:
    # Given: corrupt legacy US Day work points at exact KR source evidence.
    path = tmp_path / "cycles.sqlite3"
    legacy = _runtime(path, EMPTY_COLLECTOR, [], MarketIsolatedDayActionClient([]))
    legacy.ingest((_evidence("day_trading", 1, "us_equities"),))
    assert legacy.tick(NOW + dt.timedelta(minutes=1)).status == "failed"
    legacy.close()
    future_kr = _evidence("day_trading", 2, "kr_equities").model_copy(
        update={"available_at": NOW + dt.timedelta(days=1)}
    )
    with ResearchAgentCycleStore(path) as store:
        _ = store.append_evidence(future_kr)
        original_work = store.open_work("day_trading")[0]
        store.upsert_open_work(original_work.model_copy(update={"source_evidence_id": future_kr.evidence_id}))
        cycles_before = store.latest_cycles()
        results_before = store.results()
    delegated: list[ResearchAgentEvidenceV1] = []
    restarted = ResearchAgentRuntime(
        ResearchAgentRuntimeServices(
            ResearchAgentCycleStore(path),
            EMPTY_COLLECTOR,
            RecordingDecisionClient([]),
            RecordingArtifactActionClient([]),
            supervisor_runtime=RecordingSupervisor(delegated),
        )
    )

    # When/Then: resolution fails before supervisor or cycle persistence.
    with pytest.raises(
        InvalidSupervisorCycleResolutionError,
        match="supervisor_open_work_market_mismatch",
    ):
        restarted.tick(NOW + dt.timedelta(minutes=16))
    assert delegated == []
    assert restarted.store.latest_cycles() == cycles_before
    assert restarted.store.results() == results_before
    assert restarted.store.open_work("day_trading")[0].state is ResearchAgentOpenWorkState.OPEN
    restarted.close()
