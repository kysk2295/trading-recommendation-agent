from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_manifest_key,
)
from trading_agent.lane_contract_models import (
    LaneAccountBinding,
    LaneDailySnapshot,
    LaneManifest,
    lane_account_binding,
)
from trading_agent.lane_defaults import (
    CURRENT_INTRADAY_EXPERIMENT_SCOPES,
    DEFAULT_LANE_MANIFESTS,
    INTRADAY_MANIFEST,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_schema import LANE_REGISTRY_SCHEMA_VERSION
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryConflictError,
    LaneRegistryStore,
    LaneRegistryWriterLeaseUnavailableError,
)

NOW = dt.datetime(2026, 7, 15, 20, tzinfo=dt.UTC)


def test_registry_schema_is_append_only_and_mode_600(tmp_path: Path) -> None:
    store = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    with store.writer() as writer:
        _ = writer.register_manifest(INTRADAY_MANIFEST)

    with sqlite3.connect(store.path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        tables = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        )
        triggers = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM lane_manifests")

    assert version == (LANE_REGISTRY_SCHEMA_VERSION,)
    assert tables == {
        "lane_manifests",
        "lane_account_bindings",
        "experiment_scopes",
        "lane_daily_snapshots",
    }
    assert "lane_manifests_no_update" in triggers
    assert "lane_daily_snapshots_no_delete" in triggers
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_registry_writer_lease_is_nonblocking(tmp_path: Path) -> None:
    store = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")

    with (
        store.writer(),
        pytest.raises(LaneRegistryWriterLeaseUnavailableError),
        LaneRegistryStore(store.path).writer(),
    ):
        pass


def test_registry_exact_replay_is_idempotent_and_query_only_readable(
    tmp_path: Path,
) -> None:
    store = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    with store.writer() as writer:
        first = tuple(writer.register_manifest(manifest) for manifest in DEFAULT_LANE_MANIFESTS)
        replay = tuple(writer.register_manifest(manifest) for manifest in DEFAULT_LANE_MANIFESTS)
        scopes = tuple(writer.register_experiment_scope(scope) for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES)

    assert first == (True, True, True)
    assert replay == (False, False, False)
    assert scopes == (True, True, True, True)
    assert tuple(item.manifest for item in store.manifests()) == DEFAULT_LANE_MANIFESTS
    assert tuple(item.scope for item in store.experiment_scopes()) == CURRENT_INTRADAY_EXPERIMENT_SCOPES
    with store._reader_connection() as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM lane_manifests")


def test_registry_rejects_manifest_identity_rewrite(tmp_path: Path) -> None:
    store = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    rewritten = LaneManifest.model_validate(
        {
            **INTRADAY_MANIFEST.model_dump(),
            "registered_at": INTRADAY_MANIFEST.registered_at + dt.timedelta(seconds=1),
        }
    )

    with store.writer() as writer:
        assert writer.register_manifest(INTRADAY_MANIFEST) is True
        with pytest.raises(LaneRegistryConflictError):
            _ = writer.register_manifest(rewritten)


def test_registry_binds_only_registered_broker_lane_once(tmp_path: Path) -> None:
    store = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    binding = lane_account_binding(INTRADAY_MANIFEST, "a" * 64, "b" * 64, NOW)
    rewritten = LaneAccountBinding.model_validate(
        {
            **binding.model_dump(),
            "account_fingerprint": "c" * 64,
            "execution_ledger_fingerprint": "d" * 64,
        }
    )
    forbidden = LaneAccountBinding(
        lane_id=LaneId.MARKET_REGIME,
        account_fingerprint="e" * 64,
        execution_ledger_fingerprint="f" * 64,
        bound_at=NOW,
    )

    with store.writer() as writer:
        for manifest in DEFAULT_LANE_MANIFESTS:
            _ = writer.register_manifest(manifest)
        assert writer.bind_account(binding) is True
        assert writer.bind_account(binding) is False
        with pytest.raises(LaneRegistryConflictError):
            _ = writer.bind_account(rewritten)
        with pytest.raises(InvalidLaneRegistrySourceError):
            _ = writer.bind_account(forbidden)

    assert store.account_bindings()[0].binding == binding


def test_registry_rejects_experiment_scope_identity_rewrite(tmp_path: Path) -> None:
    store = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    scope = CURRENT_INTRADAY_EXPERIMENT_SCOPES[0]
    rewritten = scope.model_copy(update={"registered_at": scope.registered_at - dt.timedelta(minutes=1)})

    with store.writer() as writer:
        assert writer.register_experiment_scope(scope) is True
        with pytest.raises(LaneRegistryConflictError):
            _ = writer.register_experiment_scope(rewritten)


def test_registry_validates_snapshot_sources_and_lane_date_identity(
    tmp_path: Path,
) -> None:
    store = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    snapshot = _snapshot()
    rewritten = LaneDailySnapshot.model_validate(
        {
            **snapshot.model_dump(),
            "source_ledger_generation": 2,
            "source_ledger_sha256": "f" * 64,
        }
    )
    missing_scope = LaneDailySnapshot.model_validate(
        {
            **snapshot.model_dump(),
            "experiment_scope_keys": ("0" * 64,),
        }
    )

    with store.writer() as writer:
        for manifest in DEFAULT_LANE_MANIFESTS:
            _ = writer.register_manifest(manifest)
        for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES:
            _ = writer.register_experiment_scope(scope)
        assert writer.append_daily_snapshot(snapshot) is True
        assert writer.append_daily_snapshot(snapshot) is False
        with pytest.raises(LaneRegistryConflictError):
            _ = writer.append_daily_snapshot(rewritten)
        with pytest.raises(InvalidLaneRegistrySourceError):
            _ = writer.append_daily_snapshot(missing_scope)

    assert store.daily_snapshots()[0].snapshot == snapshot


def _snapshot() -> LaneDailySnapshot:
    scope = next(scope for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES if scope.hypothesis_id == "H-MOM-ORB-001")
    return LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=dt.date(2026, 7, 14),
        finalized_at=NOW,
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(experiment_scope_key(scope),),
        source_ledger_generation=1,
        source_ledger_sha256="e" * 64,
        champion_strategy_versions=(),
        data_quality_complete=True,
        allocation_eligible=False,
        incidents=(),
        conservative_equity=Decimal("30000"),
        realized_pnl=Decimal(0),
        unrealized_pnl=Decimal(0),
        planned_open_risk=Decimal(0),
        open_order_count=0,
        open_position_count=0,
    )
