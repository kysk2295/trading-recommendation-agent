from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from tests.autonomous_supervisor_fixtures import fixture_reasoner
from tests.kr_autonomous_vertical_runtime_support import (
    fixture_clock,
    fixture_tool_bindings,
    prepare_token_cache,
    provider_calls,
)
from tests.kr_autonomous_vertical_scenario import (
    collision_bar,
    events_json,
    expected_chain,
    position_path,
    reasoning_responses,
    snapshot,
    tool_call,
    trade_path,
)
from tests.test_autonomous_kr_market_tool import _append_calendar, _service_config
from tests.test_autonomous_task_models import task_fixture
from tests.test_kr_autonomous_market_service import NOW
from tests.test_kr_social_signal import _selected_posts
from trading_agent import _autonomous_kr_tool_support
from trading_agent._autonomous_supervisor_steps import DelegatePayload, ObservationPayload, parse_payload
from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices, KrVirtualStartupReconciliation
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import (
    AutonomousReasoningResponse,
)
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import AutonomousAgentRole, autonomous_task_id
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext, AutonomousToolRuntime
from trading_agent.browser_social_evidence import BrowserSocialEvidence
from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
from trading_agent.kr_autonomous_market_models import KrAutonomousMarketCorroboration
from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_models import KrSocialSignal
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar
from trading_agent.kr_virtual_position_models import KrVirtualPositionEvent
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore
from trading_agent.research_agent_cycle_models import EvidenceId

type VerticalOrder = Literal["observer_delegates", "research_combines"]


@dataclass(frozen=True, slots=True)
class VerticalResult:
    tool_order: tuple[str, ...]
    delegate_roles: tuple[AutonomousAgentRole, ...]
    decision_kinds: tuple[str, ...]
    posts: tuple[BrowserSocialEvidence, ...]
    signal: KrSocialSignal
    market: KrAutonomousMarketCorroboration
    recommendation: KrTradeRecommendation
    terminal: KrVirtualPositionEvent
    open_restart_before: str
    open_restart_after: str
    replay_before: str
    replay_after: str
    replay_json: str
    calls: tuple[tuple[str, str, str], ...]
    startup: KrVirtualStartupReconciliation
    services: KrAutonomousToolServices


def run_vertical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: VerticalOrder) -> VerticalResult:
    posts = _selected_posts()
    root = EvidenceId(posts[0].evidence_id)
    task = task_fixture(
        task_id=autonomous_task_id("day_trading", "kr_equities", root),
        root_source_evidence_id=root,
        source_evidence_ids=(root,),
        created_at=NOW,
        updated_at=NOW,
    )
    config = _service_config(tmp_path)
    _append_calendar(config, open_day=True)
    token_cache = tmp_path / "token-cache"
    prepare_token_cache(token_cache)
    services = KrAutonomousToolServices(
        browser_evidence_database=tmp_path / "browser.sqlite3",
        social_signal_database=tmp_path / "signals.sqlite3",
        task_database=tmp_path / "tasks.sqlite3",
        service_config_json=config.model_dump_json(),
        startup_at=NOW,
    )
    for post in posts:
        assert BrowserSocialEvidenceStore(services.browser_evidence_database).append(post)
    with AutonomousTaskStore(services.task_database).writer() as writer:
        assert writer.create_task(task)
    signal, market, pending, recommendation = expected_chain(task.task_id, posts)
    bar = collision_bar(recommendation)
    audit = tmp_path / "provider-calls.sqlite3"
    responses = reasoning_responses(order, signal, market, pending, recommendation)
    runtime = _runtime(tmp_path, services, responses, audit, token_cache, bar, max_steps=2)
    wake = NOW
    for _ in range(8):
        result = runtime.tick(task, wake)
        if KrVirtualPositionStore(position_path(services)).open_positions():
            break
        durable = AutonomousTaskStore(services.task_database).reader().task(task.task_id)
        step_kinds = tuple(
            parse_payload(step.payload_json).kind
            for step in AutonomousTaskStore(services.task_database).reader().steps(task.task_id)
        )
        assert result.status == "waiting" and durable is not None and durable.next_wake_at is not None, (
            result,
            durable,
            step_kinds,
        )
        wake = durable.next_wake_at
    else:
        raise AssertionError("fixture reasoner did not create an open virtual position")
    position_store = KrVirtualPositionStore(position_path(services))
    open_event = position_store.open_positions()[0]
    open_before = events_json(position_store.events(open_event.position_id))
    durable = AutonomousTaskStore(services.task_database).reader().task(task.task_id)
    assert durable is not None and durable.next_wake_at is not None
    monkeypatch.setattr(_autonomous_kr_tool_support, "observed_completed_bars", lambda *_args: ())
    restarted = KrAutonomousToolServices(
        services.browser_evidence_database,
        services.social_signal_database,
        services.task_database,
        services.service_config_json,
        services.trade_database,
        services.pending_plan_database,
        services.position_database,
        NOW,
    )
    open_after = events_json(KrVirtualPositionStore(position_path(restarted)).events(open_event.position_id))
    restarted_runtime = _runtime(tmp_path, restarted, responses, audit, token_cache, bar, max_steps=3)
    completed = restarted_runtime.tick(task, durable.next_wake_at)
    assert completed.status == "completed"
    terminal = KrVirtualPositionStore(position_path(restarted)).events(open_event.position_id)[-1]
    before = snapshot(restarted, pending.plan_id)
    terminal_restart = KrAutonomousToolServices(
        restarted.browser_evidence_database,
        restarted.social_signal_database,
        restarted.task_database,
        restarted.service_config_json,
        restarted.trade_database,
        restarted.pending_plan_database,
        restarted.position_database,
        bar.observed_at,
    )
    replay_runtime = AutonomousToolRuntime(
        fixture_tool_bindings(terminal_restart, audit, token_cache, bar), fixture_clock
    )
    replay = replay_runtime.dispatch(
        AutonomousAgentRole.POSITION,
        tool_call("kr.position.reconcile", {"position_id": open_event.position_id}),
        AutonomousToolExecutionContext(
            task_id=task.task_id,
            agent_family_id=task.agent_family_id,
            market_scope=task.market_scope,
        ),
    )
    after = snapshot(terminal_restart, pending.plan_id)
    steps = AutonomousTaskStore(services.task_database).reader().steps(task.task_id)
    payloads = tuple(parse_payload(step.payload_json) for step in steps)
    actual_signal = KrSocialSignalStore(services.social_signal_database).get(signal.signal_id)
    actual_recommendation = KrAutonomousTradeStore(trade_path(services)).event(recommendation.event_id)
    assert actual_signal is not None and isinstance(actual_recommendation, KrTradeRecommendation)
    return VerticalResult(
        tuple(payload.observation.tool_name for payload in payloads if isinstance(payload, ObservationPayload)),
        tuple(payload.role for payload in payloads if isinstance(payload, DelegatePayload)),
        tuple(json.loads(payload.response_json)["kind"] for payload in payloads if payload.kind == "decision"),
        posts,
        actual_signal,
        market,
        actual_recommendation,
        terminal,
        open_before,
        open_after,
        before,
        after,
        replay.bounded_json,
        provider_calls(audit),
        restarted.startup_reconciliation,
        terminal_restart,
    )


def _runtime(
    tmp_path: Path,
    services: KrAutonomousToolServices,
    responses: tuple[AutonomousReasoningResponse, ...],
    audit: Path,
    token_cache: Path,
    bar: KrCompletedMinuteBar,
    *,
    max_steps: int,
) -> AutonomousSupervisorRuntime:
    tools = AutonomousToolRuntime(
        fixture_tool_bindings(services, audit, token_cache, bar),
        fixture_clock,
        worker_modules=frozenset({"tests.kr_autonomous_vertical_runtime_support"}),
    )
    return AutonomousSupervisorRuntime(
        AutonomousTaskStore(services.task_database),
        AutonomousMemoryStore(tmp_path / "memories.sqlite3"),
        fixture_reasoner(tmp_path, responses),
        tools,
        lambda: NOW,
        time.monotonic,
        max_steps,
    )
