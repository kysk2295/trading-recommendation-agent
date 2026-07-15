from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_account_binding_key,
    lane_daily_snapshot_key,
    lane_manifest_key,
)
from trading_agent.lane_contract_models import (
    ExperimentScope,
    ExperimentScopeKind,
    InvalidLaneContractError,
    LaneAccountBinding,
    LaneDailySnapshot,
    LaneManifest,
    lane_account_binding,
    require_scope_registered_before_session,
)
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
    INTRADAY_MANIFEST,
    MARKET_REGIME_MANIFEST,
    SWING_MANIFEST,
)
from trading_agent.lane_policy_models import LaneId

REGISTERED_AT = dt.datetime(2026, 7, 14, tzinfo=dt.UTC)
FINALIZED_AT = dt.datetime(2026, 7, 15, 20, tzinfo=dt.UTC)


def test_default_manifests_have_deterministic_distinct_keys() -> None:
    assert tuple(manifest.lane_id for manifest in DEFAULT_LANE_MANIFESTS) == tuple(LaneId)
    keys = tuple(lane_manifest_key(manifest) for manifest in DEFAULT_LANE_MANIFESTS)

    assert len(set(keys)) == 3
    assert all(len(key) == 64 for key in keys)
    assert lane_manifest_key(INTRADAY_MANIFEST) == lane_manifest_key(INTRADAY_MANIFEST)
    assert INTRADAY_MANIFEST.ledger_namespace == "execution/intraday_momentum"
    assert SWING_MANIFEST.account_binding_mode == "forbidden"
    assert MARKET_REGIME_MANIFEST.account_binding_mode == "forbidden"


def test_only_broker_authorized_manifest_can_create_an_account_binding() -> None:
    binding = lane_account_binding(
        INTRADAY_MANIFEST,
        "a" * 64,
        "b" * 64,
        REGISTERED_AT,
    )

    assert binding.lane_id is LaneId.INTRADAY_MOMENTUM
    assert binding.paper_base_url == "https://paper-api.alpaca.markets"
    assert len(lane_account_binding_key(binding)) == 64
    assert "a" * 64 not in repr(binding)
    assert "b" * 64 not in repr(binding)

    with pytest.raises(InvalidLaneContractError):
        _ = lane_account_binding(
            MARKET_REGIME_MANIFEST,
            "c" * 64,
            "d" * 64,
            REGISTERED_AT,
        )


def test_account_binding_rejects_live_url_or_malformed_fingerprint() -> None:
    valid = lane_account_binding(
        INTRADAY_MANIFEST,
        "a" * 64,
        "b" * 64,
        REGISTERED_AT,
    )

    with pytest.raises(ValidationError):
        _ = LaneAccountBinding.model_validate(
            {
                **valid.model_dump(),
                "paper_base_url": "https://api.alpaca.markets",
            }
        )
    with pytest.raises(ValidationError):
        _ = LaneAccountBinding.model_validate(
            {
                **valid.model_dump(),
                "account_fingerprint": "not-a-fingerprint",
            }
        )


def test_cross_lane_scope_requires_a_new_preregistered_hypothesis() -> None:
    with pytest.raises(ValidationError):
        _ = ExperimentScope(
            scope_kind=ExperimentScopeKind.CROSS_LANE_HYPOTHESIS,
            hypothesis_id="H-MOM-ORB-001",
            primary_lane=LaneId.INTRADAY_MOMENTUM,
            lanes=(LaneId.INTRADAY_MOMENTUM, LaneId.MARKET_REGIME),
            source_hypothesis_ids=("H-MOM-ORB-001", "H-REGIME-VIX-001"),
            combination_rule="Apply the pre-open VIX state to every ORB candidate.",
            registered_at=REGISTERED_AT,
        )

    scope = ExperimentScope(
        scope_kind=ExperimentScopeKind.CROSS_LANE_HYPOTHESIS,
        hypothesis_id="H-CROSS-ORB-VIX-001",
        primary_lane=LaneId.INTRADAY_MOMENTUM,
        lanes=(LaneId.INTRADAY_MOMENTUM, LaneId.MARKET_REGIME),
        source_hypothesis_ids=("H-MOM-ORB-001", "H-REGIME-VIX-001"),
        combination_rule="Apply the pre-open VIX state to every ORB candidate.",
        registered_at=REGISTERED_AT,
    )

    assert len(experiment_scope_key(scope)) == 64
    require_scope_registered_before_session(scope, dt.date(2026, 7, 14))
    with pytest.raises(InvalidLaneContractError):
        require_scope_registered_before_session(
            scope.model_copy(update={"registered_at": dt.datetime(2026, 7, 14, 14, tzinfo=dt.UTC)}),
            dt.date(2026, 7, 14),
        )


def test_current_strategy_scopes_are_single_intraday_lane() -> None:
    assert {scope.hypothesis_id for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES} == {
        "H-MOM-GAP-001",
        "H-MOM-HOD-001",
        "H-MOM-ORB-001",
        "H-MOM-VWAP-001",
    }
    assert all(scope.scope_kind is ExperimentScopeKind.SINGLE_LANE for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES)
    assert all(scope.lanes == (LaneId.INTRADAY_MOMENTUM,) for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES)


def test_intraday_final_snapshot_must_be_flat() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError):
        _ = LaneDailySnapshot.model_validate(
            {
                **snapshot.model_dump(),
                "open_position_count": 1,
            }
        )


def test_allocation_eligible_snapshot_requires_quality_and_a_champion() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError):
        _ = LaneDailySnapshot.model_validate(
            {
                **snapshot.model_dump(),
                "allocation_eligible": True,
                "champion_strategy_versions": (),
            }
        )

    eligible = LaneDailySnapshot.model_validate(
        {
            **snapshot.model_dump(),
            "allocation_eligible": True,
            "champion_strategy_versions": ("orb-v1",),
        }
    )
    assert len(lane_daily_snapshot_key(eligible)) == 64


def test_signal_only_snapshot_has_zero_broker_fields() -> None:
    snapshot = _snapshot(lane_id=LaneId.MARKET_REGIME, manifest=MARKET_REGIME_MANIFEST)

    with pytest.raises(ValidationError):
        _ = LaneDailySnapshot.model_validate(
            {
                **snapshot.model_dump(),
                "conservative_equity": Decimal("1"),
            }
        )


def _snapshot(
    *,
    lane_id: LaneId = LaneId.INTRADAY_MOMENTUM,
    manifest: LaneManifest = INTRADAY_MANIFEST,
) -> LaneDailySnapshot:
    scope = next(scope for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES if scope.hypothesis_id == "H-MOM-ORB-001")
    if lane_id is LaneId.MARKET_REGIME:
        scope = ExperimentScope(
            scope_kind=ExperimentScopeKind.SINGLE_LANE,
            hypothesis_id="H-REGIME-VIX-001",
            primary_lane=LaneId.MARKET_REGIME,
            lanes=(LaneId.MARKET_REGIME,),
            registered_at=REGISTERED_AT,
        )
    return LaneDailySnapshot(
        lane_id=lane_id,
        session_date=dt.date(2026, 7, 14),
        finalized_at=FINALIZED_AT,
        manifest_key=lane_manifest_key(manifest),
        experiment_scope_keys=(experiment_scope_key(scope),),
        source_ledger_generation=1,
        source_ledger_sha256="e" * 64,
        champion_strategy_versions=(),
        data_quality_complete=True,
        allocation_eligible=False,
        incidents=(),
        conservative_equity=Decimal(0),
        realized_pnl=Decimal(0),
        unrealized_pnl=Decimal(0),
        planned_open_risk=Decimal(0),
        open_order_count=0,
        open_position_count=0,
    )
