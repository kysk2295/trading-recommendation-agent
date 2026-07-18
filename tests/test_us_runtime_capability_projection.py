from __future__ import annotations

from pathlib import Path

from tests.alpaca_sip_runtime_fleet_fixtures import (
    NOW,
    decision,
    feature_requests,
)
from tests.test_us_market_data_fleet_audit import _cycle
from trading_agent.data_capability_models import DataCorrectionPolicy, DataHealthState
from trading_agent.us_market_data_fleet_audit import build_runtime_fleet_audit
from trading_agent.us_runtime_capability_projection import (
    project_us_runtime_capability,
)


def test_ready_fleet_projects_complete_aggregate_and_owner_health(tmp_path: Path) -> None:
    result, gate = _cycle(tmp_path, gap_symbol=None)
    audit = build_runtime_fleet_audit(decision(), feature_requests(), result, gate)

    projection = project_us_runtime_capability(audit)

    assert projection.complete is True
    assert projection.cycle_id == audit.cycle_id
    assert tuple(item.ready for item in projection.owners) == (True, True)
    assert projection.capability.source_id.canonical_id == "alpaca/sip"
    assert projection.capability.health_state is DataHealthState.COMPLETE
    assert projection.capability.observed_completeness_bps == 10_000
    assert projection.capability.latest_event_received_at is None
    assert projection.capability.latest_source_heartbeat_at == NOW
    assert projection.capability.retention.correction_policy is DataCorrectionPolicy.SNAPSHOT_ONLY
    assert projection.entitlement.effective_from <= NOW


def test_owner_gap_projects_degraded_aggregate_without_fake_event(tmp_path: Path) -> None:
    result, gate = _cycle(tmp_path, gap_symbol="BBB")
    audit = build_runtime_fleet_audit(decision(), feature_requests(), result, gate)

    projection = project_us_runtime_capability(audit)

    assert projection.complete is False
    assert tuple(item.ready for item in projection.owners) == (True, False)
    assert projection.capability.health_state is DataHealthState.DEGRADED
    assert projection.capability.observed_completeness_bps == 5_000
    assert projection.capability.latest_event_received_at is None
