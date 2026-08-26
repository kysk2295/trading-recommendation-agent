from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import pytest

from tests.test_research_agent_runtime import (
    EMPTY_COLLECTOR,
    NOW,
    _evidence,
    _production_actions,
    _runtime,
)
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import ResearchAgentWakeKind
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_runtime import (
    ResearchAgentRuntimeLeaseUnavailableError,
    research_agent_runtime_lease,
)
from trading_agent.research_agent_runtime_support import scheduled_evidence


@pytest.mark.parametrize(
    ("family", "source_key"),
    (
        ("opportunity_manager", "opportunity.blocked.snapshot_unavailable"),
        ("market_context", "market_context.blocked.snapshot_unavailable"),
        ("day_trading", "day.blocked.completed_bar_unavailable"),
    ),
)
def test_primary_blocked_evidence_persists_no_action_before_model_call(
    tmp_path: Path,
    family: AgentFamilyId,
    source_key: str,
) -> None:
    calls: list[AgentFamilyId] = []
    runtime = _runtime(tmp_path / "cycles.sqlite3", EMPTY_COLLECTOR, calls)
    runtime.ingest((_evidence(family, 1).model_copy(update={"source_key": source_key}),))

    tick = runtime.tick(NOW + dt.timedelta(minutes=2))
    stored = runtime.store.results()
    runtime.close()

    assert tick.status == "no_action"
    assert tick.agent_family_id == family
    assert tick.model_calls == 0
    assert len(stored) == 1
    assert stored[0].reason == source_key
    assert stored[0].continuation == "Wait for current-session Primary evidence that passes source admission."
    assert stored[0].next_wake_kind is ResearchAgentWakeKind.NEW_EVIDENCE
    assert stored[0].next_wake_at is None
    assert stored[0].artifact_refs == ()
    assert calls == []


def test_closed_session_primary_schedule_persists_no_action_before_model_call(tmp_path: Path) -> None:
    calls: list[AgentFamilyId] = []
    runtime = _runtime(tmp_path / "cycles.sqlite3", EMPTY_COLLECTOR, calls)
    runtime.ingest((scheduled_evidence("market_context", NOW, 30),))

    tick = runtime.tick(NOW)
    stored = runtime.store.results()
    runtime.close()

    assert tick.status == "no_action"
    assert tick.agent_family_id == "market_context"
    assert tick.model_calls == 0
    assert stored[0].reason == "market_context.regular_session_closed"
    assert stored[0].continuation == "Wait until the next New York regular session."
    assert stored[0].next_wake_kind is ResearchAgentWakeKind.SCHEDULED
    assert stored[0].next_wake_at == dt.datetime(2026, 8, 3, 13, 30, tzinfo=dt.UTC)
    assert stored[0].artifact_refs == ()
    assert calls == []


def test_research_blocked_evidence_rejects_prose_only_completion(tmp_path: Path) -> None:
    calls: list[AgentFamilyId] = []
    evidence = _evidence("swing_trading", 1).model_copy(update={"source_key": "swing.blocked.shadow_evidence_empty"})
    runtime = _runtime(tmp_path / "cycles.sqlite3", EMPTY_COLLECTOR, calls, _production_actions())
    runtime.ingest((evidence,))

    tick = runtime.tick(NOW)
    stored = runtime.store.results()
    runtime.close()

    assert tick.status == "failed"
    assert tick.model_calls == 1
    assert stored[0].reason == "prose_only_result"
    assert stored[0].artifact_refs == ()
    assert calls == ["swing_trading"]


def test_interrupted_cycle_replays_once_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    evidence = _evidence("swing_trading", 1)
    seed = ResearchAgentCycleStore(path)
    assert seed.append_evidence(evidence)
    stored = seed.runnable_evidence("swing_trading", NOW)
    interrupted = seed.start_cycle(stored[0], NOW)
    seed.close()
    calls: list[AgentFamilyId] = []
    runtime = _runtime(path, EMPTY_COLLECTOR, calls)

    replay = runtime.tick(NOW + dt.timedelta(minutes=1))
    idle = runtime.tick(NOW + dt.timedelta(minutes=1, seconds=30))
    events = runtime.store.cycle_events(interrupted.cycle_id)
    runtime.close()

    assert replay.recovered_cycles == 1
    assert replay.agent_family_id == "swing_trading"
    assert idle.status == "idle"
    assert [event.state.value for event in events] == ["started", "interrupted", "started", "completed"]
    assert calls == ["swing_trading"]


def test_runtime_lease_is_private_nonblocking_and_reusable(tmp_path: Path) -> None:
    lease = (tmp_path / "private" / "research-runtime.lock").absolute()

    with research_agent_runtime_lease(lease):
        assert stat.S_IMODE(lease.stat().st_mode) == 0o600
        with pytest.raises(ResearchAgentRuntimeLeaseUnavailableError), research_agent_runtime_lease(lease):
            raise AssertionError

    with research_agent_runtime_lease(lease):
        assert lease.is_file()
