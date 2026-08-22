from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from trading_agent.day_agent_task_models import DayAgentAction
from trading_agent.day_agent_tool_models import DayAgentToolArguments
from trading_agent.day_agent_tool_runtime import DayAgentToolBinding, DayAgentToolRuntime

if TYPE_CHECKING:
    from trading_agent.us_day_source_models import CanonicalUsDaySource


def build_us_day_read_tools(
    source: CanonicalUsDaySource,
    clock: Callable[[], dt.datetime],
) -> DayAgentToolRuntime:
    evidence_refs = tuple(item.canonical_id for item in source.situation.evidence_refs)

    def inspect_situation(arguments: DayAgentToolArguments) -> str:
        del arguments
        return source.situation.model_dump_json()

    def read_catalysts(arguments: DayAgentToolArguments) -> str:
        symbol = arguments.root.get("symbol")
        catalysts = tuple(
            catalyst.model_dump(mode="json")
            for theme in source.situation.themes
            for catalyst in theme.catalysts
            if symbol is None or symbol in catalyst.symbols
        )
        return json.dumps(catalysts, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    def compare_leaders(arguments: DayAgentToolArguments) -> str:
        del arguments
        leaders = tuple(
            leader.model_dump(mode="json")
            for theme in source.situation.themes
            for leader in theme.leaders
        )
        return json.dumps(leaders, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    return DayAgentToolRuntime(
        (
            DayAgentToolBinding(
                DayAgentAction.INSPECT_SITUATION,
                frozenset(),
                inspect_situation,
                evidence_refs,
            ),
            DayAgentToolBinding(
                DayAgentAction.READ_CATALYSTS,
                frozenset({"symbol"}),
                read_catalysts,
                evidence_refs,
            ),
            DayAgentToolBinding(
                DayAgentAction.COMPARE_LEADERS,
                frozenset(),
                compare_leaders,
                evidence_refs,
            ),
        ),
        clock,
    )


__all__ = ("build_us_day_read_tools",)
