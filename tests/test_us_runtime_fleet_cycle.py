from __future__ import annotations

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
from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.us_intraday_volume_profile_artifact import IntradayVolumeProfileArtifactStore
from trading_agent.us_market_data_fleet import RuntimeFleetStatus
from trading_agent.us_market_data_fleet_audit_store import RuntimeFleetAuditStore
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_runtime_fleet_cycle import (
    ProfileArtifactBinding,
    RuntimeFleetCycleError,
    RuntimeFleetCycleRequest,
    bind_runtime_profiles,
    execute_runtime_fleet_cycle,
    prepare_runtime_fleet_cycle,
)
from trading_agent.us_runtime_policy_scope import (
    RuntimePolicyScopeRequest,
    prepare_runtime_policy_scope,
)
from trading_agent.us_subscription_models import BroadScannerCandidate, BroadScannerSnapshot

PROJECT = Path(__file__).resolve().parents[1]
FOUNDATION = PROJECT / "examples/data/us-orb-data-foundation-v1.json"


class _Scanner:
    def __init__(self, bundle: UsOpportunityScannerBundle) -> None:
        self.bundle = bundle

    def latest_bundle(self) -> UsOpportunityScannerBundle:
        return self.bundle


def test_scanner_profile_fleet_gate_and_audit_complete_one_ready_cycle(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        symbol = request.url.params["symbols"]
        return httpx2.Response(
            200,
            json={"bars": {symbol: wire_bars(symbol, 35)}, "next_page_token": None},
        )

    prepared = prepare_runtime_fleet_cycle(_Scanner(_bundle()), _request(tmp_path))
    audit_store = RuntimeFleetAuditStore(tmp_path / "fleet-audit.sqlite3")
    result = execute_runtime_fleet_cycle(
        prepared,
        fleet(tmp_path / "runtime", respond),
        audit_store,
    )

    assert result.fleet.status is RuntimeFleetStatus.READY
    assert result.audit.gate_status == "ready"
    assert result.audit_appended is True
    assert audit_store.latest() == result.audit
    assert len(requests) == 2
    assert all(item.method == "GET" for item in requests)
    assert all(item.url.host == "data.alpaca.markets" for item in requests)


def test_profile_coverage_mismatch_blocks_before_fleet_or_http(tmp_path: Path) -> None:
    cycle_request = _request(tmp_path)
    missing = RuntimeFleetCycleRequest(
        cycle_request.evaluated_at,
        cycle_request.active,
        cycle_request.cooldowns,
        cycle_request.policy_config,
        cycle_request.profiles[:1],
    )

    with pytest.raises(RuntimeFleetCycleError, match="runtime fleet cycle input is invalid"):
        _ = prepare_runtime_fleet_cycle(_Scanner(_bundle()), missing)


def test_policy_scope_exposes_desired_and_completed_minute_before_profile_io(tmp_path: Path) -> None:
    policy = decision()
    scope = prepare_runtime_policy_scope(
        _Scanner(_bundle()),
        RuntimePolicyScopeRequest(NOW, (), (), policy.config),
    )

    assert tuple(item.instrument_id for item in scope.decision.desired) == (
        "alpaca:asset-aaa",
        "alpaca:asset-bbb",
    )
    assert scope.completed_minute == 35
    with pytest.raises(RuntimeFleetCycleError, match="runtime fleet cycle input is invalid"):
        _ = bind_runtime_profiles(scope, ())
    assert not (tmp_path / "profiles").exists()


def test_expired_opportunity_blocks_before_profile_read(tmp_path: Path) -> None:
    base = opportunity()
    expired = base.model_copy(update={"valid_until": NOW})
    current = _bundle()
    bundle = UsOpportunityScannerBundle(expired, current.snapshot, current.foundation)

    with pytest.raises(RuntimeFleetCycleError, match="runtime fleet cycle input is invalid"):
        _ = prepare_runtime_fleet_cycle(_Scanner(bundle), _request(tmp_path, write_profiles=False))


def _bundle() -> UsOpportunityScannerBundle:
    policy = decision()
    base = opportunity()

    return UsOpportunityScannerBundle(
        base,
        BroadScannerSnapshot(
            policy.identity,
            policy.candidate_observed_at,
            tuple(
                BroadScannerCandidate(
                    item.instrument_id,
                    item.symbol,
                    base.candidates[index].score,
                    index + 1,
                )
                for index, item in enumerate(policy.desired)
            ),
        ),
        load_data_foundation_manifest(FOUNDATION),
    )


def _request(tmp_path: Path, *, write_profiles: bool = True) -> RuntimeFleetCycleRequest:
    paths: list[ProfileArtifactBinding] = []
    if write_profiles:
        for request in feature_requests():
            root = tmp_path / "profiles" / request.instrument_id.replace(":", "_")
            path = IntradayVolumeProfileArtifactStore(root).append(request.volume_profile)
            paths.append(ProfileArtifactBinding(request.instrument_id, path))
    policy = decision()
    return RuntimeFleetCycleRequest(
        NOW,
        (),
        (),
        policy.config,
        tuple(paths),
    )
