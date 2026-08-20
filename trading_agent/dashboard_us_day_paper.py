from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_agent.dashboard_paper_finalized_terminal import (
    FinalizedPaperAuthority,
    FinalizedPaperAuthorityFailure,
    read_finalized_paper_authority,
)
from trading_agent.dashboard_paper_lifecycle import protective_plan_terminal
from trading_agent.execution_ledger_identity import ExecutionLedgerSnapshotIdentity
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_store import ExecutionStore
from trading_agent.hermes_delivery_errors import InvalidHermesDeliveryStoreError
from trading_agent.hermes_delivery_models import HermesDeliveryEvent, HermesDeliveryKind
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.lane_contract_keys import lane_daily_snapshot_key, lane_manifest_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_policy_models import LaneId, LaneOrderAuthority
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)
from trading_agent.paper_execution_models import BrokerOrderEventType, IntentId
from trading_agent.us_equity_calendar import NEW_YORK


@dataclass(frozen=True, slots=True)
class FinalizedPaperProjectionBundle:
    ledger: ReconciliationLedger
    identity: ExecutionLedgerSnapshotIdentity
    snapshot: LaneDailySnapshot
    authority: FinalizedPaperAuthority | FinalizedPaperAuthorityFailure
    hermes_events: tuple[HermesDeliveryEvent, ...]
    hermes_valid: bool


def read_finalized_paper_bundle(
    outputs: Path,
    *,
    now: dt.datetime,
) -> FinalizedPaperProjectionBundle | FinalizedPaperAuthorityFailure:
    try:
        path = outputs / "lane_control" / "lane_registry.sqlite3"
        wal_path = path.with_name(f"{path.name}-wal")
        if wal_path.exists() and wal_path.stat().st_size > 0:
            return FinalizedPaperAuthorityFailure("corrupt", "paper_source_wal_active")
        snapshot = _latest_snapshot(outputs)
        if snapshot is None:
            return FinalizedPaperAuthorityFailure("unavailable", "paper_finalized_ledger_missing")
        if snapshot.finalized_at > now + dt.timedelta(minutes=5):
            return FinalizedPaperAuthorityFailure("corrupt", "paper_future_observation")
        if not snapshot.data_quality_complete:
            return FinalizedPaperAuthorityFailure("blocked", "paper_verification_incomplete")
        execution_path = outputs / "paper" / "execution.sqlite3"
        if not execution_path.is_file():
            return FinalizedPaperAuthorityFailure("unavailable", "paper_finalized_execution_missing")
        store = ExecutionStore(execution_path)
        identity = store.ledger_snapshot_identity()
        if identity.generation != snapshot.source_ledger_generation or identity.sha256 != snapshot.source_ledger_sha256:
            return FinalizedPaperAuthorityFailure("corrupt", "paper_epoch_mismatch")
        ledger = store.reconciliation_ledger()
        if store.ledger_snapshot_identity() != identity:
            return FinalizedPaperAuthorityFailure("corrupt", "paper_epoch_mismatch")
        authority = read_finalized_paper_authority(outputs, snapshot, now)
        try:
            events = HermesDeliveryReader(outputs / "hermes" / "delivery.sqlite3").events()
            hermes_valid = True
        except (InvalidHermesDeliveryStoreError, OSError, sqlite3.Error, ValueError):
            events = ()
            hermes_valid = False
    except (
        InvalidLaneRegistrySourceError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        UnsupportedLaneRegistrySchemaError,
        ValueError,
    ):
        return FinalizedPaperAuthorityFailure("corrupt", "paper_finalized_ledger_invalid")
    return FinalizedPaperProjectionBundle(ledger, identity, snapshot, authority, events, hermes_valid)


def canonical_day_exit_event(
    bundle: FinalizedPaperProjectionBundle,
    *,
    intent_id: IntentId,
    symbol: str,
) -> HermesDeliveryEvent | None:
    if isinstance(bundle.authority, FinalizedPaperAuthorityFailure):
        return None
    ledger = bundle.ledger
    snapshot = bundle.snapshot
    receipt = bundle.authority.receipt
    intents = tuple(item for item in ledger.intents if item.intent_id == intent_id)
    states = tuple(item for item in ledger.order_states if item.intent_id == intent_id)
    plans = tuple(item for item in ledger.protective_oco_plans if item.plan.parent_intent_id == intent_id)
    if (
        snapshot.lane_id is not LaneId.INTRADAY_MOMENTUM
        or snapshot.open_order_count != 0
        or snapshot.open_position_count != 0
        or bundle.identity.generation != receipt.source_ledger_generation
        or bundle.identity.sha256 != receipt.source_ledger_sha256
        or len(intents) != 1
        or len(states) != 1
        or len(plans) != 1
        or intents[0].symbol != symbol
        or intent_id not in ledger.filled_intent_ids
        or intent_id in ledger.unresolved_intent_ids
        or ledger.pending_trade_update_receipt_keys
        or ledger.unrecovered_trade_update_quarantine_keys
        or not states[0].terminal
        or not states[0].complete_fill
        or not states[0].execution_detail_complete
        or states[0].anomaly_reasons
        or states[0].terminal_event_types != (BrokerOrderEventType.FILL,)
        or not protective_plan_terminal(plans[0], ledger, snapshot.finalized_at)
    ):
        return None
    intent_ref = (f"intent:{intent_id}",)
    strategy = intents[0].strategy_version
    intent_created_at = dt.datetime.fromisoformat(intents[0].created_at)
    actionables = tuple(
        event
        for event in bundle.hermes_events
        if event.kind is HermesDeliveryKind.ACTIONABLE
        and event.market_id == "us_equities"
        and event.lane_id == "intraday_momentum"
        and event.agent_family == "day_trading"
        and event.instrument_id == symbol
        and event.strategy_version == strategy
        and event.status == "current_quote_validated"
        and event.evidence_refs == intent_ref
        and intent_created_at <= event.occurred_at <= snapshot.finalized_at
        and event.occurred_at.astimezone(NEW_YORK).date() == snapshot.session_date
    )
    exits = tuple(
        event
        for actionable in actionables
        for event in bundle.hermes_events
        if event.kind is HermesDeliveryKind.EXIT
        and event.root_delivery_id == actionable.delivery_id
        and event.market_id == actionable.market_id
        and event.lane_id == actionable.lane_id
        and event.agent_family == actionable.agent_family
        and event.instrument_id == actionable.instrument_id
        and event.strategy_version == actionable.strategy_version
        and event.status == "completed"
        and event.evidence_refs == intent_ref
        and actionable.occurred_at <= event.occurred_at <= snapshot.finalized_at
        and event.occurred_at.astimezone(NEW_YORK).date() == snapshot.session_date
    )
    return max(exits, key=lambda event: (event.occurred_at, event.delivery_id), default=None)


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


__all__ = (
    "FinalizedPaperProjectionBundle",
    "canonical_day_exit_event",
    "read_finalized_paper_bundle",
)
