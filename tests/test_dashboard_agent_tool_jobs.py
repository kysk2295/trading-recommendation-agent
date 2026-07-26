from __future__ import annotations

import pytest
from pydantic import ValidationError

from trading_agent.dashboard_agent_tool_jobs import (
    ToolStep,
    validate_tool_steps,
)


def test_tool_plan_accepts_research_only_steps() -> None:
    # Given: an allowlisted evidence-to-candidate plan
    steps = (
        ToolStep(tool="read_evidence", purpose="bind source receipts"),
        ToolStep(tool="write_candidate", purpose="record candidate evidence"),
        ToolStep(tool="run_tests", purpose="verify candidate"),
    )

    # When / Then: the plan remains typed and ordered
    assert validate_tool_steps(steps) == steps


@pytest.mark.parametrize("tool", ["provider_order", "paper_order", "shell", "network_post"])
def test_tool_plan_rejects_mutation_and_unbounded_tools(tool: str) -> None:
    # Given: a provider-mutating or unbounded tool request
    # When / Then: it fails before any subprocess launch
    with pytest.raises(ValidationError):
        ToolStep.model_validate({"tool": tool, "purpose": "forbidden"})
