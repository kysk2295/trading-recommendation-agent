from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from tests.kr_autonomous_tool_test_support import thesis
from tests.test_autonomous_kr_market_tool import _service_config
from tests.test_autonomous_task_models import task_fixture
from tests.test_kr_autonomous_market_service import NOW, _calendar, _receipts
from tests.test_kr_social_signal import _selected_posts
from trading_agent._autonomous_supervisor_steps import ObservationPayload, payload_json, plain_step
from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices, kr_tool_bindings
from trading_agent.autonomous_reasoning import AutonomousToolArguments, AutonomousToolCall, AutonomousToolObservation
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousTaskState, autonomous_task_id
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext, AutonomousToolRuntime
from trading_agent.browser_social_evidence import BrowserSocialEvidence
from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
from trading_agent.kr_autonomous_market_models import KrAutonomousMarketCorroboration
from trading_agent.kr_autonomous_market_service import KrCorroborationProjectionInput, project_kr_corroboration
from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_models import KrSocialSignal
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar
from trading_agent.kr_virtual_position_models import KrVirtualPositionEvent
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore
from trading_agent.research_agent_cycle_models import EvidenceId
from trading_agent.signal_contract_models import EvidenceRef

type VerticalOrder = Literal["observer_first", "research_repeat"]


@dataclass(frozen=True, slots=True)
class VerticalResult:
    tool_order: tuple[str, ...]
    role_order: tuple[AutonomousAgentRole, ...]
    posts: tuple[BrowserSocialEvidence, ...]
    signal: KrSocialSignal
    market: KrAutonomousMarketCorroboration
    recommendation: KrTradeRecommendation
    terminal: KrVirtualPositionEvent
    exact_restart_json: str
    mutation_counts: dict[str, int]
    services: KrAutonomousToolServices


def run_vertical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    order: VerticalOrder,
) -> VerticalResult:
    posts = _selected_posts()
    root = EvidenceId(posts[0].evidence_id)
    task = task_fixture(
        task_id=autonomous_task_id("day_trading", "kr_equities", root),
        root_source_evidence_id=root,
        source_evidence_ids=(root,),
        created_at=NOW,
        updated_at=NOW,
    )
    services = KrAutonomousToolServices(
        browser_evidence_database=tmp_path / "browser.sqlite3",
        social_signal_database=tmp_path / "signals.sqlite3",
        task_database=tmp_path / "tasks.sqlite3",
        service_config_json=_service_config(tmp_path).model_dump_json(),
        startup_at=NOW,
    )
    trade_database = services.trade_database
    position_database = services.position_database
    assert trade_database is not None and position_database is not None
    browser_store = BrowserSocialEvidenceStore(services.browser_evidence_database)
    for post in posts:
        assert browser_store.append(post)
    with AutonomousTaskStore(services.task_database).writer() as writer:
        assert writer.create_task(task)
    counters = {"kis_read": 0, "kis_mutation": 0, "ls_mutation": 0, "alpaca": 0, "account": 0}

    def collect(signal: KrSocialSignal, _config, _now: dt.datetime) -> KrAutonomousMarketCorroboration:
        counters["kis_read"] += 1
        return project_kr_corroboration(
            KrCorroborationProjectionInput(
                signal=signal, calendar_snapshot=_calendar(), receipts=_receipts(), observed_at=NOW
            )
        )

    monkeypatch.setattr("trading_agent.autonomous_kr_tools.utc_now", lambda: NOW)
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.collect_and_project_kr_corroboration", collect)
    runtime = AutonomousToolRuntime(kr_tool_bindings(services), lambda: NOW)
    context = AutonomousToolExecutionContext(
        task_id=task.task_id,
        agent_family_id="day_trading",
        market_scope="kr_equities",
    )
    normalize_role = AutonomousAgentRole.MARKET_OBSERVER if order == "observer_first" else AutonomousAgentRole.RESEARCH
    market_role = AutonomousAgentRole.OPPORTUNITY if order == "observer_first" else AutonomousAgentRole.RESEARCH
    observations: list[tuple[AutonomousAgentRole, AutonomousToolObservation]] = []
    normalized = runtime.dispatch(
        normalize_role,
        _call(
            "social.signal.normalize",
            {
                "claim_summary": "Independent reporting supports a semiconductor demand acceleration claim.",
                "evidence_ids_json": json.dumps(
                    tuple(sorted(post.evidence_id for post in posts)), separators=(",", ":")
                ),
                "symbol": "005930",
                "theme": "Semiconductor demand",
            },
        ),
        context,
    )
    signal_id = str(json.loads(normalized.bounded_json)["signal_id"])
    market_observation = runtime.dispatch(
        market_role,
        _call("kr.market.corroborate", {"signal_id": signal_id, "symbol": "005930"}),
        context,
    )
    observations.append((market_role, market_observation))
    _record_observation(services.task_database, task, market_observation)
    market = KrAutonomousMarketCorroboration.model_validate_json(
        json.dumps(json.loads(market_observation.bounded_json)["market"])
    )
    if order == "research_repeat":
        repeated = runtime.dispatch(
            AutonomousAgentRole.OPPORTUNITY,
            _call("kr.market.corroborate", {"signal_id": signal_id, "symbol": "005930"}),
            context,
        )
        observations.append((AutonomousAgentRole.OPPORTUNITY, repeated))
        _record_observation(services.task_database, task, repeated)
    signal = KrSocialSignalStore(services.social_signal_database).get(signal_id)
    assert signal is not None
    planned = runtime.dispatch(
        AutonomousAgentRole.TRADING,
        _call(
            "kr.trade.plan",
            {
                "thesis_json": json.dumps(
                    thesis(signal, market).model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                )
            },
        ),
        context,
    )
    plan_id = str(json.loads(planned.bounded_json)["plan_id"])
    approved = runtime.dispatch(AutonomousAgentRole.CRITIC, _call("critic.request", {"plan_id": plan_id}), context)
    recommendation_id = str(json.loads(approved.bounded_json)["event_id"])
    recommendation = KrAutonomousTradeStore(trade_database).event(recommendation_id)
    assert isinstance(recommendation, KrTradeRecommendation)
    executed = runtime.dispatch(
        AutonomousAgentRole.TRADING,
        _call("kr.virtual.execute", {"recommendation_id": recommendation_id}),
        context,
    )
    position_id = str(json.loads(executed.bounded_json)["position_id"])
    bar = _collision_bar(recommendation)
    monkeypatch.setattr("trading_agent._autonomous_kr_tool_support.observed_completed_bars", lambda *_args: (bar,))
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.utc_now", lambda: bar.observed_at)
    reconciled = runtime.dispatch(
        AutonomousAgentRole.POSITION,
        _call("kr.position.reconcile", {"position_id": position_id}),
        context,
    )
    terminal = KrVirtualPositionStore(position_database).events(position_id)[-1]
    restarted = KrAutonomousToolServices(
        browser_evidence_database=services.browser_evidence_database,
        social_signal_database=services.social_signal_database,
        task_database=services.task_database,
        service_config_json=services.service_config_json,
        trade_database=trade_database,
        pending_plan_database=services.pending_plan_database,
        position_database=position_database,
        startup_at=bar.observed_at,
    )
    replay = AutonomousToolRuntime(kr_tool_bindings(restarted), lambda: bar.observed_at).dispatch(
        AutonomousAgentRole.POSITION,
        _call("kr.position.reconcile", {"position_id": position_id}),
        context,
    )
    roles = (
        normalize_role,
        *(role for role, _ in observations),
        AutonomousAgentRole.TRADING,
        AutonomousAgentRole.CRITIC,
        AutonomousAgentRole.TRADING,
        AutonomousAgentRole.POSITION,
    )
    tools = (
        "social.signal.normalize",
        *("kr.market.corroborate" for _ in observations),
        "kr.trade.plan",
        "critic.request",
        "kr.virtual.execute",
        "kr.position.reconcile",
    )
    assert json.loads(reconciled.bounded_json)["event_id"] == terminal.event_id
    return VerticalResult(
        tools, roles, posts, signal, market, recommendation, terminal, replay.bounded_json, counters, restarted
    )


def _record_observation(path: Path, task, observation: AutonomousToolObservation) -> None:
    store = AutonomousTaskStore(path)
    sequence = len(store.reader().steps(task.task_id)) + 1
    step = plain_step(
        task,
        sequence,
        NOW,
        AutonomousTaskState.OBSERVING,
        payload_json(ObservationPayload(decision_hash="a" * 64, observation=observation)),
        task.source_evidence_ids,
        tuple(sorted({*task.evidence_refs, *observation.evidence_refs})),
    )
    with store.writer() as writer:
        assert writer.append_step(step)


def _collision_bar(recommendation: KrTradeRecommendation) -> KrCompletedMinuteBar:
    start = recommendation.timestamp.astimezone(NOW.tzinfo).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    observed = start + dt.timedelta(minutes=1, seconds=1)
    return KrCompletedMinuteBar(
        symbol=recommendation.symbol,
        start_at=start,
        end_at=start + dt.timedelta(minutes=1),
        observed_at=observed,
        open=recommendation.entry,
        high=recommendation.targets[0],
        low=recommendation.stop,
        close=recommendation.entry,
        volume=100,
        trading_value_krw=recommendation.entry * Decimal(100),
        evidence_ref=EvidenceRef(namespace="kr/fixture", record_id="future-collision", observed_at=observed),
    )


def _call(name: str, values: dict[str, str]) -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name=name,
        args=AutonomousToolArguments(values),
        reason="Exercise the bounded autonomous KR fixture through its public tool contract.",
    )
