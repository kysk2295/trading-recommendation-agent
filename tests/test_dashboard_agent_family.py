from __future__ import annotations

import pytest

from trading_agent.dashboard_agent_family import (
    PRIMARY_AGENT_FAMILIES,
    InvalidAgentFamilyRegistryError,
    build_agent_family_registry,
)


def test_registry_has_exactly_six_families_and_eighteen_capabilities() -> None:
    # Given: the product registry
    # When: every family and capability is counted
    registry = build_agent_family_registry(PRIMARY_AGENT_FAMILIES)

    # Then: only the six research identities expose all three channels
    assert tuple(item.family_id for item in registry) == PRIMARY_AGENT_FAMILIES
    assert sum(len(item.capabilities) for item in registry) == 18
    assert len({item.memory_namespace for item in registry}) == 6


@pytest.mark.parametrize(
    "family_ids",
    [
        (*PRIMARY_AGENT_FAMILIES, "delivery"),
        (*PRIMARY_AGENT_FAMILIES, "research"),
        (*PRIMARY_AGENT_FAMILIES, "allocation_manager"),
        (*PRIMARY_AGENT_FAMILIES, PRIMARY_AGENT_FAMILIES[0]),
        PRIMARY_AGENT_FAMILIES[:-1],
    ],
)
def test_registry_rejects_operational_extra_duplicate_and_missing_ids(
    family_ids: tuple[str, ...],
) -> None:
    # Given: an identity list that is not the exact primary-family contract
    # When: it crosses the registry boundary
    # Then: it is rejected instead of becoming a product identity
    with pytest.raises(InvalidAgentFamilyRegistryError):
        build_agent_family_registry(family_ids)
