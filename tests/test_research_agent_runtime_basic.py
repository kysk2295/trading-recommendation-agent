from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_research_agent_runtime import (
    EMPTY_COLLECTOR,
    NOW,
    MarketIsolatedDayActionClient,
    RecordingArtifactActionClient,
    StaticCollector,
    _evidence,
    _runtime,
)
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.research_agent_actions import ResearchAgentActionContext
from trading_agent.research_agent_cycle_models import ResearchAgentDecisionKind
from trading_agent.research_agent_sources import (
    ResearchAgentSourceCollectionBatch,
    ResearchAgentSourceFailure,
)


def test_runtime_passes_action_context_with_evidence_subjects_and_observation_time(tmp_path: Path) -> None:
    contexts: list[ResearchAgentActionContext] = []
    runtime = _runtime(
        tmp_path / "cycles.sqlite3",
        EMPTY_COLLECTOR,
        [],
        RecordingArtifactActionClient(contexts),
    )
    runtime.ingest((_evidence("swing_trading", 1),))

    tick = runtime.tick(NOW + dt.timedelta(minutes=2))
    stored = runtime.store.results()
    runtime.close()

    assert tick.status == "completed"
    assert stored[0].decision_kind is ResearchAgentDecisionKind.PROPOSE_HYPOTHESIS
    assert contexts[0].evidence[0].bounded_payload_json == '{"sequence":1}'
    assert contexts[0].decision.subject_refs == contexts[0].evidence[0].subject_refs
    assert contexts[0].observed_at == NOW + dt.timedelta(minutes=2)


def test_idle_ticks_do_not_call_the_model(tmp_path: Path) -> None:
    calls: list[AgentFamilyId] = []
    runtime = _runtime(
        tmp_path / "cycles.sqlite3",
        StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(), failures=())),
        calls,
    )

    first = runtime.tick(NOW)
    second = runtime.tick(NOW + dt.timedelta(seconds=30))
    runtime.close()

    assert first.status == second.status == "idle"
    assert first.model_calls == second.model_calls == 0
    assert calls == []


def test_two_families_run_separate_cycles_and_restart_without_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    calls: list[AgentFamilyId] = []
    runtime = _runtime(
        path,
        StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(), failures=())),
        calls,
    )
    runtime.ingest((_evidence("opportunity_manager", 1), _evidence("systematic_quant", 1)))

    first = runtime.tick(NOW + dt.timedelta(minutes=2))
    second = runtime.tick(NOW + dt.timedelta(minutes=2, seconds=30))
    runtime.close()
    restarted = _runtime(
        path,
        StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(), failures=())),
        calls,
    )
    third = restarted.tick(NOW + dt.timedelta(minutes=3))
    results = restarted.store.results()
    restarted.close()

    assert {first.agent_family_id, second.agent_family_id} == {"opportunity_manager", "systematic_quant"}
    assert third.status == "idle"
    assert len(results) == 2
    assert calls == ["systematic_quant", "opportunity_manager"]


def test_bounded_cycle_processes_each_family_once_and_replay_is_idle(tmp_path: Path) -> None:
    path = tmp_path / "cycles.sqlite3"
    calls: list[AgentFamilyId] = []
    runtime = _runtime(path, EMPTY_COLLECTOR, calls)
    runtime.ingest(tuple(_evidence(family, 1) for family in PRIMARY_AGENT_FAMILIES))

    first = runtime.cycle(NOW + dt.timedelta(minutes=2))
    cursors = tuple(runtime.store.cursor(family) for family in PRIMARY_AGENT_FAMILIES)
    open_work = tuple(runtime.store.open_work(family) for family in PRIMARY_AGENT_FAMILIES)
    runtime.close()
    restarted = _runtime(path, EMPTY_COLLECTOR, calls)
    replay = restarted.cycle(NOW + dt.timedelta(minutes=3))
    results = restarted.store.results()
    restarted.close()

    assert first.status == "complete"
    assert tuple(item.agent_family_id for item in first.outcomes) == PRIMARY_AGENT_FAMILIES
    assert first.model_calls == 6
    assert first.recovered_cycles == 0
    assert all(cursor > 0 for cursor in cursors)
    assert all(len(items) == 1 and items[0].state.value == "terminal" for items in open_work)
    assert replay.status == "idle"
    assert replay.outcomes == ()
    assert len(results) == 6
    assert calls == list(PRIMARY_AGENT_FAMILIES)


def test_bounded_cycle_does_not_debounce_fresh_one_minute_opportunity_past_expiry(
    tmp_path: Path,
) -> None:
    calls: list[AgentFamilyId] = []
    runtime = _runtime(tmp_path / "cycles.sqlite3", EMPTY_COLLECTOR, calls)
    runtime.ingest(tuple(_evidence(family, 1) for family in PRIMARY_AGENT_FAMILIES))

    cycle = runtime.cycle(NOW + dt.timedelta(seconds=30))
    runtime.close()

    assert cycle.status == "complete"
    assert tuple(item.agent_family_id for item in cycle.outcomes) == PRIMARY_AGENT_FAMILIES


def test_source_failure_is_isolated_and_never_calls_the_model(tmp_path: Path) -> None:
    calls: list[AgentFamilyId] = []
    collector = StaticCollector(
        ResearchAgentSourceCollectionBatch(
            evidence=(),
            failures=(
                ResearchAgentSourceFailure(
                    agent_family_id="market_context",
                    reason="market_context_source_invalid",
                    observed_at=NOW,
                ),
            ),
        )
    )
    runtime = _runtime(tmp_path / "cycles.sqlite3", collector, calls)

    result = runtime.tick(NOW)
    stored = runtime.store.results()
    runtime.close()

    assert result.status == "failed"
    assert result.agent_family_id == "market_context"
    assert result.model_calls == 0
    assert stored[0].reason == "market_context_source_invalid"
    assert calls == []


def test_us_day_failure_backoff_and_open_work_do_not_block_or_leak_into_kr(
    tmp_path: Path,
) -> None:
    contexts: list[ResearchAgentActionContext] = []
    runtime = _runtime(
        tmp_path / "cycles.sqlite3",
        EMPTY_COLLECTOR,
        [],
        MarketIsolatedDayActionClient(contexts),
    )
    runtime.ingest((_evidence("day_trading", 1, "us_equities"),))
    us = runtime.tick(NOW + dt.timedelta(minutes=1))
    runtime.ingest((_evidence("day_trading", 2, "kr_equities"),))
    kr = runtime.tick(NOW + dt.timedelta(minutes=2))
    work = runtime.store.open_work("day_trading")
    runtime.close()

    assert (us.status, kr.status) == ("failed", "completed")
    assert tuple(context.cycle.market_id for context in contexts) == (
        "us_equities",
        "kr_equities",
    )
    assert contexts[1].open_work == ()
    assert {(item.work_id, item.state.value) for item in work} == {
        ("actor-state.day_trading.us_equities", "open"),
        ("actor-state.day_trading.kr_equities", "terminal"),
    }
