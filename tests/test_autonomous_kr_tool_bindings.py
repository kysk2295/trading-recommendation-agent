from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent._autonomous_supervisor_wire import build_tools, tools_wire
from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices, kr_tool_bindings
from trading_agent.autonomous_reasoning import AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_supervisor_service import utc_clock
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousTaskId
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolExecutionContext,
    AutonomousToolRuntime,
    AutonomousToolRuntimeError,
)


def test_kr_tool_bindings_expose_exact_role_scoped_signatures(tmp_path: Path) -> None:
    services = KrAutonomousToolServices(
        tmp_path / "browser.sqlite3", tmp_path / "signals.sqlite3", tmp_path / "tasks.sqlite3", "{}"
    )
    bindings = {binding.name: binding for binding in kr_tool_bindings(services)}
    assert tuple(sorted(bindings)) == (
        "critic.request",
        "kr.market.corroborate",
        "kr.position.reconcile",
        "kr.trade.plan",
        "kr.virtual.execute",
        "social.signal.normalize",
    )
    assert bindings["social.signal.normalize"].allowed_arguments == frozenset(
        {"claim_summary", "evidence_ids_json", "symbol", "theme"}
    )
    assert bindings["social.signal.normalize"].allowed_roles == frozenset(
        {AutonomousAgentRole.MARKET_OBSERVER, AutonomousAgentRole.RESEARCH}
    )
    assert bindings["kr.market.corroborate"].allowed_arguments == frozenset({"signal_id", "symbol"})
    assert bindings["kr.market.corroborate"].allowed_roles == frozenset(
        {AutonomousAgentRole.OPPORTUNITY, AutonomousAgentRole.RESEARCH}
    )
    assert bindings["kr.trade.plan"].allowed_arguments == frozenset({"thesis_json"})
    assert bindings["kr.trade.plan"].allowed_roles == frozenset({AutonomousAgentRole.TRADING})
    assert bindings["critic.request"].allowed_arguments == frozenset({"plan_id"})
    assert bindings["critic.request"].allowed_roles == frozenset({AutonomousAgentRole.CRITIC})
    assert bindings["kr.virtual.execute"].allowed_arguments == frozenset({"recommendation_id"})
    assert bindings["kr.virtual.execute"].allowed_roles == frozenset({AutonomousAgentRole.TRADING})
    assert bindings["kr.position.reconcile"].allowed_arguments == frozenset({"position_id"})
    assert bindings["kr.position.reconcile"].allowed_roles == frozenset({AutonomousAgentRole.POSITION})
    runtime = AutonomousToolRuntime(
        tuple(bindings.values()),
        utc_clock,
        worker_modules=frozenset({"trading_agent.autonomous_kr_tools", "trading_agent.autonomous_supervisor_service"}),
    )
    wire = tools_wire(runtime)
    assert all(binding.invoke.module == "trading_agent.autonomous_kr_tools" for binding in wire.bindings)
    assert build_tools(wire).allowed_tool_names == tuple(sorted(bindings))


def test_kr_tool_denies_wrong_role_and_incomplete_arguments_before_store_access(tmp_path: Path) -> None:
    services = KrAutonomousToolServices(
        tmp_path / "browser.sqlite3", tmp_path / "signals.sqlite3", tmp_path / "tasks.sqlite3", "{}"
    )
    runtime = AutonomousToolRuntime(kr_tool_bindings(services), utc_clock)
    context = AutonomousToolExecutionContext(
        task_id=AutonomousTaskId("a" * 64), agent_family_id="day_trading", market_scope="kr_equities"
    )
    denied = _call(
        "social.signal.normalize",
        {"claim_summary": "independent claim", "evidence_ids_json": "[]", "symbol": "005930", "theme": "AI"},
    )
    incomplete = _call(
        "social.signal.normalize", {"claim_summary": "independent claim", "evidence_ids_json": "[]", "symbol": "005930"}
    )
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_authority_denied"):
        _ = runtime.dispatch(AutonomousAgentRole.TRADING, denied, context)
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_authority_denied"):
        _ = runtime.dispatch(
            AutonomousAgentRole.MARKET_OBSERVER,
            _call("social.signal.normalize", {**denied.args.root, "extra": "denied"}),
            context,
        )
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_invocation_failed"):
        _ = runtime.dispatch(
            AutonomousAgentRole.MARKET_OBSERVER, denied, context.model_copy(update={"market_scope": "us_equities"})
        )
    with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_invocation_failed"):
        _ = runtime.dispatch(AutonomousAgentRole.MARKET_OBSERVER, incomplete, context)
    assert not services.browser_evidence_database.exists()
    assert not services.social_signal_database.exists()
    assert not services.task_database.exists()


def _call(name: str, values: dict[str, str]) -> AutonomousToolCall:
    return AutonomousToolCall(
        tool_name=name, args=AutonomousToolArguments(values), reason="Exercise bounded KR tool contracts."
    )
