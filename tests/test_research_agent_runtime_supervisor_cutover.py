from __future__ import annotations

import datetime as dt
from pathlib import Path

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
