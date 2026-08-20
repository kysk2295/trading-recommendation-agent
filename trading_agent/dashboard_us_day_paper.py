from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_agent.dashboard_paper_finalized_terminal import (
    FinalizedPaperAuthorityFailure,
    read_finalized_paper_authority,
)
from trading_agent.execution_ledger_identity import ExecutionLedgerSnapshotIdentity
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_delivery_models import HermesDeliveryEvent
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.lane_contract_keys import lane_daily_snapshot_key, lane_manifest_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_policy_models import LaneId, LaneOrderAuthority
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)


@dataclass(frozen=True, slots=True)
class VerifiedDayPaperLedger:
    ledger: ReconciliationLedger
    identity: ExecutionLedgerSnapshotIdentity
    snapshot: LaneDailySnapshot
    hermes_events: tuple[HermesDeliveryEvent, ...]


def read_verified_day_paper_ledger(outputs: Path, *, now: dt.datetime) -> VerifiedDayPaperLedger | None:
    try:
        snapshot = _latest_snapshot(outputs)
        if snapshot is None or not snapshot.data_quality_complete:
            return None
        authority = read_finalized_paper_authority(outputs, snapshot, now)
        if isinstance(authority, FinalizedPaperAuthorityFailure):
            return None
        store = ExecutionStore(outputs / "paper" / "execution.sqlite3")
        identity = store.ledger_snapshot_identity()
        receipt = authority.receipt
        if identity.generation != receipt.source_ledger_generation or identity.sha256 != receipt.source_ledger_sha256:
            return None
        ledger = store.reconciliation_ledger()
        if store.ledger_snapshot_identity() != identity:
            return None
        events = HermesDeliveryReader(outputs / "hermes" / "delivery.sqlite3").events()
    except (
        InvalidLaneRegistrySourceError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        UnsupportedLaneRegistrySchemaError,
        ValueError,
    ):
        return None
    return VerifiedDayPaperLedger(ledger, identity, snapshot, events)


def _latest_snapshot(outputs: Path) -> LaneDailySnapshot | None:
    reader = LaneRegistryReader(outputs / "lane_control" / "lane_registry.sqlite3")
    manifests = {
        str(item.manifest_key): item.manifest
        for item in reader.manifests()
        if item.manifest.execution_policy.order_authority
        in (LaneOrderAuthority.ALPACA_PAPER, LaneOrderAuthority.SHADOW_ONLY)
    }
    candidates = tuple(
        item
        for item in reader.daily_snapshots()
        if item.snapshot.lane_id in (LaneId.INTRADAY_MOMENTUM, LaneId.SWING_MOMENTUM)
    )
    if any(
        item.snapshot_key != lane_daily_snapshot_key(item.snapshot)
        or item.snapshot.manifest_key not in manifests
        or manifests[item.snapshot.manifest_key].lane_id is not item.snapshot.lane_id
        or lane_manifest_key(manifests[item.snapshot.manifest_key]) != item.snapshot.manifest_key
        for item in candidates
    ):
        raise ValueError
    latest_session = max((item.snapshot.session_date for item in candidates), default=None)
    snapshots = tuple(item.snapshot for item in candidates if item.snapshot.session_date == latest_session)
    if len(snapshots) > 1:
        raise ValueError
    return snapshots[0] if snapshots else None


__all__ = ("VerifiedDayPaperLedger", "read_verified_day_paper_ledger")
