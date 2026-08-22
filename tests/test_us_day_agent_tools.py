from __future__ import annotations

from tests.test_us_day_situation_projection import EVALUATED_AT, _inputs, _project
from tests.test_us_day_thesis_runtime import _markets
from trading_agent.day_agent_task_models import DayAgentAction
from trading_agent.day_agent_tool_models import DayAgentToolArguments, DayAgentToolCall
from trading_agent.us_day_agent_service import CanonicalUsDaySource
from trading_agent.us_day_agent_tools import build_us_day_read_tools


def _call(action: DayAgentAction, **arguments: str) -> DayAgentToolCall:
    return DayAgentToolCall(
        action=action,
        arguments=DayAgentToolArguments(arguments),
        reason="Inspect bounded canonical evidence before the next decision.",
    )


def test_canonical_day_tools_expose_situation_catalysts_and_leader_comparison() -> None:
    # Given: one current canonical source.
    source = CanonicalUsDaySource(situation=_project(_inputs()), current_markets=_markets())
    tools = build_us_day_read_tools(source, lambda: EVALUATED_AT)

    # When: the reasoning loop performs its three human-trader reads.
    situation = tools.dispatch(_call(DayAgentAction.INSPECT_SITUATION))
    catalysts = tools.dispatch(_call(DayAgentAction.READ_CATALYSTS, symbol="NVDA"))
    leaders = tools.dispatch(_call(DayAgentAction.COMPARE_LEADERS))

    # Then: only the canonical, evidence-bound read surface is exposed.
    assert tools.allowed_tool_names == (
        "compare_leaders",
        "inspect_situation",
        "read_catalysts",
    )
    assert '"session_id":"XNYS-2026-08-20"' in situation.bounded_json
    assert '"symbols":["AMD","NVDA"]' in catalysts.bounded_json
    assert '"rank":1' in leaders.bounded_json


def test_canonical_day_tools_do_not_expose_broker_or_mutation_actions() -> None:
    # Given / When: tools are composed from a current source.
    source = CanonicalUsDaySource(situation=_project(_inputs()), current_markets=_markets())
    names = build_us_day_read_tools(source, lambda: EVALUATED_AT).allowed_tool_names

    # Then: every exposed action is a read-only research action.
    assert not {"account", "position", "order", "mutation", "sizing"}.intersection(names)
