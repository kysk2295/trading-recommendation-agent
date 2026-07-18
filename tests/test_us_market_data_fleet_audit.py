from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import httpx2
import pytest

from tests.alpaca_sip_runtime_fleet_fixtures import (
    NOW,
    decision,
    feature_requests,
    fleet,
    opportunity,
    wire_bars,
)
from trading_agent.us_feature_evidence_projection import (
    project_us_opportunity_with_feature_evidence,
)
from trading_agent.us_market_data_fleet_audit import (
    RuntimeFleetAuditError,
    build_runtime_fleet_audit,
)
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore


def test_ready_fleet_cycle_round_trips_append_only_audit(tmp_path: Path) -> None:
    result, gate = _cycle(tmp_path, gap_symbol=None)
    record = build_runtime_fleet_audit(decision(), feature_requests(), result, gate)
    store = RuntimeFleetAuditStore(tmp_path / "fleet-audit.sqlite3")

    assert store.append(record) is True
    assert store.append(record) is False
    assert store.latest() == record
    assert len(record.owners) == 2
    assert all(item.profile_evidence_sha256 for item in record.owners)
    assert record.gate_status == "ready"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_degraded_owner_and_blocked_gate_are_preserved(tmp_path: Path) -> None:
    result, gate = _cycle(tmp_path, gap_symbol="BBB")

    record = build_runtime_fleet_audit(decision(), feature_requests(), result, gate)

    assert tuple(item.owner_status for item in record.owners) == ("ready", "blocked")
    assert record.gate_status == "blocked"
    assert record.gate_reason == "missing_evidence"


def test_tampered_audit_payload_fails_replay(tmp_path: Path) -> None:
    result, gate = _cycle(tmp_path, gap_symbol=None)
    store = RuntimeFleetAuditStore(tmp_path / "fleet-audit.sqlite3")
    assert store.append(build_runtime_fleet_audit(decision(), feature_requests(), result, gate))
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER runtime_fleet_audit_no_update")
        connection.execute("UPDATE runtime_fleet_audit SET payload_json = X'7B7D'")
        connection.commit()

    with pytest.raises(RuntimeFleetAuditError, match="invalid"):
        store.latest()


def _cycle(tmp_path: Path, *, gap_symbol: str | None):
    def respond(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.params["symbols"]
        bars = wire_bars(symbol, 35)
        if symbol == gap_symbol:
            bars = (*bars[:1], *bars[2:])
        return httpx2.Response(
            200,
            json={"bars": {symbol: bars}, "next_page_token": None},
        )

    result = fleet(tmp_path / "runtime", respond).run_cycle(
        decision(),
        feature_requests(),
    )
    gate = project_us_opportunity_with_feature_evidence(
        opportunity(),
        result.bindings,
        evaluated_at=NOW,
    )
    return result, gate
