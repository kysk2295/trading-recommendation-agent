from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, NewType

AgentFamilyId = Literal[
    "opportunity_manager",
    "day_trading",
    "swing_trading",
    "systematic_quant",
    "derivatives_research",
    "market_context",
]
AgentCapability = Literal["conversation", "directed_tool", "autonomous_research"]
AgentCapabilities = tuple[
    Literal["conversation"],
    Literal["directed_tool"],
    Literal["autonomous_research"],
]
MemoryNamespace = NewType("MemoryNamespace", str)

PRIMARY_AGENT_FAMILIES: Final[tuple[AgentFamilyId, ...]] = (
    "opportunity_manager",
    "day_trading",
    "swing_trading",
    "systematic_quant",
    "derivatives_research",
    "market_context",
)
AGENT_CAPABILITIES: Final[AgentCapabilities] = (
    "conversation",
    "directed_tool",
    "autonomous_research",
)

_ROLES: Final[dict[AgentFamilyId, str]] = {
    "opportunity_manager": "source-bound opportunity research",
    "day_trading": "intraday hypothesis and evidence research",
    "swing_trading": "multi-session hypothesis and evidence research",
    "systematic_quant": "systematic experiment research",
    "derivatives_research": "derivatives evidence research",
    "market_context": "macro and market-context research",
}


@dataclass(frozen=True, slots=True)
class InvalidAgentFamilyRegistryError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class AgentFamilyDefinition:
    family_id: AgentFamilyId
    role: str
    memory_namespace: MemoryNamespace
    capabilities: AgentCapabilities


def build_agent_family_registry(
    family_ids: tuple[str, ...],
) -> tuple[AgentFamilyDefinition, ...]:
    if family_ids != PRIMARY_AGENT_FAMILIES or len(set(family_ids)) != len(family_ids):
        raise InvalidAgentFamilyRegistryError(reason="exact_primary_agent_families_required")
    return tuple(
        AgentFamilyDefinition(
            family_id=family_id,
            role=_ROLES[family_id],
            memory_namespace=MemoryNamespace(f"research-family:{family_id}:memory-v1"),
            capabilities=AGENT_CAPABILITIES,
        )
        for family_id in PRIMARY_AGENT_FAMILIES
    )


AGENT_FAMILY_REGISTRY: Final = build_agent_family_registry(PRIMARY_AGENT_FAMILIES)

__all__ = (
    "AGENT_CAPABILITIES",
    "AGENT_FAMILY_REGISTRY",
    "PRIMARY_AGENT_FAMILIES",
    "AgentCapabilities",
    "AgentCapability",
    "AgentFamilyDefinition",
    "AgentFamilyId",
    "InvalidAgentFamilyRegistryError",
    "MemoryNamespace",
    "build_agent_family_registry",
)
