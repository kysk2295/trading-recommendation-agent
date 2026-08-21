from __future__ import annotations

import datetime as dt

from trading_agent.research_agent_service_runtime import ResearchAgentServiceReport


def test_tick_report_preserves_persistent_day_multi_call_usage() -> None:
    # Given / When
    report = ResearchAgentServiceReport(
        config_sha256="a" * 64,
        operation="tick",
        status="no_action",
        agent_family_id="day_trading",
        cycle_id="b" * 64,
        result_status="no_action",
        model_calls=2,
        recovered_cycles=0,
        projected_results=0,
        systematic_input_status="blocked",
        systematic_input_sha256=None,
        systematic_foundation_sha256=None,
        family_runtime=(),
        next_wake_kind=None,
        next_wake_at=None,
        observed_at=dt.datetime(2026, 8, 21, tzinfo=dt.UTC),
    )

    # Then
    assert report.model_calls == 2
