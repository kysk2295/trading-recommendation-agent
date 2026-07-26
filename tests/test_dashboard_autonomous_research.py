from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.dashboard_autonomous_research import (
    AutonomousTriggerV1,
    trigger_fixture,
)


def test_trigger_accepts_typed_authority_with_freshness_and_isolation() -> None:
    # Given: an authorized, source-bound new-data event
    payload = trigger_fixture(now=dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC))

    # When: it crosses the strict trigger boundary
    trigger = AutonomousTriggerV1.model_validate(payload)

    # Then: the family, authority, source, budget, and pinned environment survive parsing
    assert trigger.agent_family_id == "systematic_quant"
    assert trigger.authority == "source_receipt"
    assert trigger.source_receipt_ids == ("source-receipt-001",)
    assert trigger.environment_spec.allowed_write_roots == ("experiment",)
    assert trigger.budget_envelope.max_model_processes == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trigger_type", "filesystem_noise"),
        ("agent_family_id", "delivery"),
        ("authority", "launchd"),
        ("interactive_session_id", "private-session-canary"),
    ],
)
def test_trigger_rejects_untyped_alias_and_session_fields(field: str, value: str) -> None:
    # Given: an otherwise valid trigger with one forbidden boundary value
    payload = trigger_fixture(now=dt.datetime(2026, 7, 26, 8, tzinfo=dt.UTC))
    payload[field] = value

    # When / Then: strict parsing fails before model execution can be considered
    with pytest.raises(ValidationError):
        AutonomousTriggerV1.model_validate(payload)
