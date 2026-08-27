from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from tests.kr_autonomous_tool_test_support import thesis as _thesis
from tests.test_autonomous_kr_market_tool import _service_config
from tests.test_autonomous_task_models import task_fixture
from tests.test_kr_autonomous_market_service import NOW, _calendar, _receipts
from tests.test_kr_social_signal import _selected_posts
from trading_agent._autonomous_supervisor_steps import ObservationPayload, payload_json, plain_step
from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices, kr_tool_bindings
from trading_agent.autonomous_reasoning import AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousTaskId,
    AutonomousTaskState,
    autonomous_task_id,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolExecutionContext,
    AutonomousToolRuntime,
    AutonomousToolRuntimeError,
)
from trading_agent.browser_social_evidence_store import BrowserSocialEvidenceStore
from trading_agent.kr_autonomous_market_models import KrAutonomousMarketCorroboration
from trading_agent.kr_autonomous_market_service import KrCorroborationProjectionInput, project_kr_corroboration
from trading_agent.kr_autonomous_pending_plan_models import KrAutonomousPendingPlan, pending_plan_id
from trading_agent.kr_autonomous_pending_plan_store import KrAutonomousPendingPlanStore
from trading_agent.kr_autonomous_trade_models import KrNoTradeReason
from trading_agent.kr_autonomous_trade_planner import finalize_kr_autonomous_trade
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_models import KrSocialSignal
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.research_agent_cycle_models import EvidenceId


def test_kr_tools_recover_market_observation_after_restart_and_critic_alone_approves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: exact durable browser evidence, a task rooted in that evidence, and a read-only market fixture.
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
    )
    assert services.trade_database is not None and services.pending_plan_database is not None
    for post in posts:
        assert BrowserSocialEvidenceStore(services.browser_evidence_database).append(post)
    with AutonomousTaskStore(services.task_database).writer() as writer:
        assert writer.create_task(task)
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.utc_now", lambda: NOW)

    def collect(signal: KrSocialSignal, _config, _now) -> KrAutonomousMarketCorroboration:
        return project_kr_corroboration(
            KrCorroborationProjectionInput(
                signal=signal,
                calendar_snapshot=_calendar(),
                receipts=_receipts(),
                observed_at=NOW,
            )
        )

    monkeypatch.setattr("trading_agent.autonomous_kr_tools.collect_and_project_kr_corroboration", collect)
    context = AutonomousToolExecutionContext(
        task_id=task.task_id,
        agent_family_id="day_trading",
        market_scope="kr_equities",
    )
    runtime = AutonomousToolRuntime(kr_tool_bindings(services), lambda: NOW)

    # When: social evidence is normalized, market truth is observed twice, and a restarted runtime plans it.
    normalized = runtime.dispatch(
        AutonomousAgentRole.MARKET_OBSERVER,
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
    signal_id = json.loads(normalized.bounded_json)["signal_id"]
    first = runtime.dispatch(
        AutonomousAgentRole.OPPORTUNITY,
        _call("kr.market.corroborate", {"signal_id": signal_id, "symbol": "005930"}),
        context,
    )
    second = runtime.dispatch(
        AutonomousAgentRole.RESEARCH,
        _call("kr.market.corroborate", {"signal_id": signal_id, "symbol": "005930"}),
        context,
    )
    market = KrAutonomousMarketCorroboration.model_validate_json(json.dumps(json.loads(first.bounded_json)["market"]))
    step = plain_step(
        task,
        1,
        NOW,
        AutonomousTaskState.OBSERVING,
        payload_json(ObservationPayload(decision_hash="a" * 64, observation=first)),
        task.source_evidence_ids,
        tuple(sorted({*task.evidence_refs, *first.evidence_refs})),
    )
    with AutonomousTaskStore(services.task_database).writer() as writer:
        assert writer.append_step(step)
    signal = KrSocialSignalStore(services.social_signal_database).get(signal_id)
    assert signal is not None
    thesis = _thesis(signal, market)
    restarted = AutonomousToolRuntime(kr_tool_bindings(services), lambda: NOW)
    plan = restarted.dispatch(
        AutonomousAgentRole.TRADING,
        _call(
            "kr.trade.plan",
            {"thesis_json": json.dumps(thesis.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)},
        ),
        context,
    )
    plan_payload = json.loads(plan.bounded_json)
    pending_store = KrAutonomousPendingPlanStore(services.pending_plan_database)
    first_pending = pending_store.plan(plan_payload["plan_id"])
    assert first_pending is not None
    collision_request = first_pending.request.model_copy(update={"next_wake_at": NOW + dt.timedelta(minutes=2)})
    collision_draft = KrAutonomousPendingPlan.model_construct(
        plan_id="", request=collision_request, proposal=first_pending.proposal
    )
    collision = KrAutonomousPendingPlan.model_validate(
        collision_draft.model_copy(update={"plan_id": pending_plan_id(collision_draft)}).model_dump(mode="python")
    )
    assert pending_store.append(collision)
    second_plan_payload = {"plan_id": collision.plan_id}
    assert KrAutonomousTradeStore(services.trade_database).events() == ()
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.utc_now", lambda: market.valid_until)
    with pytest.raises(AutonomousToolRuntimeError):
        _ = restarted.dispatch(
            AutonomousAgentRole.CRITIC,
            _call("critic.request", {"plan_id": plan_payload["plan_id"]}),
            context,
        )
    assert KrAutonomousTradeStore(services.trade_database).events() == ()
    monkeypatch.setattr("trading_agent.autonomous_kr_tools.utc_now", lambda: NOW)
    pending = KrAutonomousPendingPlanStore(services.pending_plan_database).plan(second_plan_payload["plan_id"])
    assert pending is not None
    assert KrAutonomousTradeStore(services.trade_database).append(
        finalize_kr_autonomous_trade(pending.request, pending.proposal, second_plan_payload["plan_id"])
    )
    approval = restarted.dispatch(
        AutonomousAgentRole.CRITIC,
        _call("critic.request", {"plan_id": second_plan_payload["plan_id"]}),
        context,
    )
    replay = AutonomousToolRuntime(kr_tool_bindings(services), lambda: NOW).dispatch(
        AutonomousAgentRole.CRITIC,
        _call("critic.request", {"plan_id": second_plan_payload["plan_id"]}),
        context,
    )
    first_approval = restarted.dispatch(
        AutonomousAgentRole.CRITIC,
        _call("critic.request", {"plan_id": plan_payload["plan_id"]}),
        context,
    )

    # Then: repeatable corroboration stays bounded, planning exposes no approval, and only Critic returns it.
    assert json.loads(second.bounded_json)["market"]["corroboration_id"] == market.corroboration_id
    assert plan_payload["status"] == "pending_critic"
    assert "approve" not in plan.bounded_json.lower()
    assert "recommend" not in plan.bounded_json.lower()
    assert json.loads(approval.bounded_json)["status"] == "APPROVED"
    assert replay.bounded_json == approval.bounded_json
    events = KrAutonomousTradeStore(services.trade_database).events()
    assert [event.event_id for event in events] == [
        json.loads(approval.bounded_json)["event_id"],
        json.loads(first_approval.bounded_json)["event_id"],
    ]
    assert [event.plan_id for event in events] == [second_plan_payload["plan_id"], plan_payload["plan_id"]]
    assert events[-1].previous_event_id == events[0].event_id
    monkeypatch.setattr(
        "trading_agent.autonomous_kr_tools.precritic_no_trade_reasons",
        lambda _request: (KrNoTradeReason.MISSING_SPREAD,),
    )
    no_trade = restarted.dispatch(
        AutonomousAgentRole.TRADING,
        _call(
            "kr.trade.plan",
            {"thesis_json": json.dumps(thesis.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)},
        ),
        context,
    )
    no_trade_payload = json.loads(no_trade.bounded_json)
    assert no_trade_payload["status"] == "NO_TRADE"
    assert no_trade_payload["reason_codes"] == "missing_spread"
    assert "next_wake_at" in no_trade_payload
    assert KrAutonomousPendingPlanStore(services.pending_plan_database).plan(no_trade_payload["plan_id"]) is None
    other_task = task.model_copy(update={"task_id": AutonomousTaskId("b" * 64)})
    with AutonomousTaskStore(services.task_database).writer() as writer:
        assert writer.create_task(other_task)
    with pytest.raises(AutonomousToolRuntimeError):
        _ = restarted.dispatch(
            AutonomousAgentRole.CRITIC,
            _call("critic.request", {"plan_id": no_trade_payload["plan_id"]}),
            context.model_copy(update={"task_id": other_task.task_id}),
        )
    with sqlite3.connect(services.pending_plan_database) as connection:
        connection.execute("DROP TRIGGER kr_autonomous_pending_plans_no_update")
        connection.execute(
            "UPDATE kr_autonomous_pending_plans SET payload_json='{}' WHERE plan_id=?",
            (plan_payload["plan_id"],),
        )
    with pytest.raises(AutonomousToolRuntimeError):
        _ = restarted.dispatch(
            AutonomousAgentRole.CRITIC,
            _call("critic.request", {"plan_id": plan_payload["plan_id"]}),
            context,
        )
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_authority_denied"):
        _ = restarted.dispatch(
            AutonomousAgentRole.TRADING,
            _call("critic.request", {"plan_id": plan_payload["plan_id"]}),
            context,
        )


def _call(name: str, values: dict[str, str]) -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name=name,
        args=AutonomousToolArguments(values),
        reason="Exercise the exact bounded autonomous KR tool contract through its public runtime.",
    )
