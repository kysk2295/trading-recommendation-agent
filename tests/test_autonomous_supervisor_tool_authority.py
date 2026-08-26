from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_autonomous_supervisor_adapter import _evidence
from tests.test_autonomous_supervisor_service import NOW, _defer_client
from tests.test_research_agent_service_cli import _config
from trading_agent.autonomous_reasoning import AutonomousAgentRole, AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_supervisor_service import build_autonomous_supervisor
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolExecutionContext,
    AutonomousToolRuntimeError,
)


def test_foundation_tools_reject_model_supplied_cross_task_identity(tmp_path: Path) -> None:
    adapter = build_autonomous_supervisor(_config(tmp_path), client=_defer_client(), clock=lambda: NOW)
    first = adapter.tick(_evidence("day_trading", "a", subjects=("005930",)), NOW)
    second = adapter.tick(_evidence("day_trading", "b", subjects=("000660",)), NOW)
    assert first.task_id is not None and second.task_id is not None
    current = AutonomousToolExecutionContext(
        task_id=first.task_id,
        agent_family_id="day_trading",
        market_scope="kr_equities",
    )
    role = AutonomousAgentRole.SUPERVISOR

    evidence = adapter.runtime.tools.dispatch(
        role,
        AutonomousToolCall(
            tool_name="evidence.read",
            args=AutonomousToolArguments({}),
            reason="Read only the current task evidence.",
        ),
        current,
    )
    history = adapter.runtime.tools.dispatch(
        role,
        AutonomousToolCall(
            tool_name="task.history",
            args=AutonomousToolArguments({}),
            reason="Read only the current task history.",
        ),
        current,
    )

    for tool_name in ("evidence.read", "task.history"):
        for argument_name in ("task_id", "current_task_id"):
            with pytest.raises(AutonomousToolRuntimeError, match="autonomous_tool_authority_denied"):
                adapter.runtime.tools.dispatch(
                    role,
                    AutonomousToolCall(
                        tool_name=tool_name,
                        args=AutonomousToolArguments({argument_name: str(second.task_id)}),
                        reason="Attempt to read another task through a model-supplied identity.",
                    ),
                    current,
                )
    assert json.loads(evidence.bounded_json)["evidence"][0]["evidence_id"] == "a" * 64
    assert len(json.loads(history.bounded_json)["steps"]) == len(
        adapter.runtime.tasks.reader().steps(first.task_id)
    )
