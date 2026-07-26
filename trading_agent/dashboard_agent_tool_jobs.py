from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

AutonomousTool = Literal[
    "read_evidence",
    "write_candidate",
    "run_tests",
    "inspect_git",
]
NetworkPolicy = Literal["none", "model_provider_only", "public_read_only"]

ALLOWED_AUTONOMOUS_TOOLS: Final[tuple[AutonomousTool, ...]] = (
    "read_evidence",
    "write_candidate",
    "run_tests",
    "inspect_git",
)


@dataclass(frozen=True, slots=True)
class InvalidAutonomousToolPlanError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class ToolStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: AutonomousTool
    purpose: str = Field(min_length=1, max_length=120)


def validate_tool_steps(steps: tuple[ToolStep, ...]) -> tuple[ToolStep, ...]:
    if not steps or len(steps) > 8:
        raise InvalidAutonomousToolPlanError(reason="bounded_tool_plan_required")
    if any(step.tool not in ALLOWED_AUTONOMOUS_TOOLS for step in steps):
        raise InvalidAutonomousToolPlanError(reason="forbidden_autonomous_tool")
    return steps


__all__ = (
    "ALLOWED_AUTONOMOUS_TOOLS",
    "AutonomousTool",
    "InvalidAutonomousToolPlanError",
    "NetworkPolicy",
    "ToolStep",
    "validate_tool_steps",
)
