from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_kr_autonomous_trade_planner import _request
from trading_agent.autonomous_kr_tools import critic_tool
from trading_agent.autonomous_reasoning import AutonomousToolArguments
from trading_agent.autonomous_task_models import AutonomousTaskId
from trading_agent.autonomous_tool_runtime import AutonomousToolExecutionContext, AutonomousToolInvocationError
from trading_agent.kr_autonomous_pending_plan_models import KrAutonomousPendingPlan, pending_plan_id
from trading_agent.kr_autonomous_trade_planner import plan_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_proposal import propose_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import InvalidKrAutonomousTradeStoreError, KrAutonomousTradeStore


def _pending() -> KrAutonomousPendingPlan:
    request = _request()
    proposal, failure = propose_kr_autonomous_trade(request)
    assert proposal is not None and failure is None
    draft = KrAutonomousPendingPlan.model_construct(plan_id="", request=request, proposal=proposal)
    return KrAutonomousPendingPlan.model_validate(
        draft.model_copy(update={"plan_id": pending_plan_id(draft)}).model_dump(mode="python")
    )


def _invoke(plan: KrAutonomousPendingPlan) -> str:
    return critic_tool(
        AutonomousToolArguments({"plan_id": plan.plan_id}),
        AutonomousToolExecutionContext(
            task_id=AutonomousTaskId(plan.request.thesis.task_id),
            agent_family_id="day_trading",
            market_scope="kr_equities",
        ),
        browser_evidence_database="unused",
        social_signal_database="unused",
        task_database="unused",
        service_config_json="{}",
        trade_database="unused",
        pending_plan_database="unused",
    )


def test_critic_denies_forged_pending_lineage_before_trade_append(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _pending()
    appended: list[object] = []
    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.trusted_task",
        lambda *_: SimpleNamespace(task_id=plan.request.thesis.task_id, root_source_evidence_id="f" * 64),
    )
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.observed_market", lambda *_: plan.request.market)
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.utc_now", lambda: plan.request.evaluated_at)
    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.KrSocialSignalStore",
        lambda *_: SimpleNamespace(get=lambda *_: plan.request.social_signal),
    )
    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.KrAutonomousPendingPlanStore",
        lambda *_: SimpleNamespace(plan=lambda *_: plan),
    )
    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.KrAutonomousTradeStore",
        lambda *_: SimpleNamespace(events=lambda: (), append=appended.append),
    )
    with pytest.raises(AutonomousToolInvocationError):
        _ = _invoke(plan)
    assert appended == []


def test_critic_retries_two_changed_tails_then_appends_valid_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _pending()
    store = KrAutonomousTradeStore(tmp_path / "trade.sqlite3")
    first = plan_kr_autonomous_trade(_request())
    second = plan_kr_autonomous_trade(_request().model_copy(update={"previous_event_id": first.event_id}))
    original = store.append
    attempts = 0

    def append(event):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            assert original(first)
            raise InvalidKrAutonomousTradeStoreError
        if attempts == 2:
            assert original(second)
            raise InvalidKrAutonomousTradeStoreError
        return original(event)

    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.trusted_task",
        lambda *_: SimpleNamespace(
            task_id=plan.request.thesis.task_id, root_source_evidence_id=plan.request.social_signal.evidence_ids[0]
        ),
    )
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.observed_market", lambda *_: plan.request.market)
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.utc_now", lambda: plan.request.evaluated_at)
    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.KrSocialSignalStore",
        lambda *_: SimpleNamespace(get=lambda *_: plan.request.social_signal),
    )
    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.KrAutonomousPendingPlanStore",
        lambda *_: SimpleNamespace(plan=lambda *_: plan),
    )
    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.KrAutonomousTradeStore",
        lambda *_: SimpleNamespace(events=store.events, append=append, event=store.event),
    )
    _ = _invoke(plan)
    events = store.events()
    assert attempts == 3 and events[-1].plan_id == plan.plan_id and events[-1].previous_event_id == second.event_id
