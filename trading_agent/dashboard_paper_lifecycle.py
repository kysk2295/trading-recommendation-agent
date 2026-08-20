from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.dashboard_models_v2 import WorkspaceItemV2
from trading_agent.execution_ledger_identity import ExecutionLedgerSnapshotIdentity
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_execution_models import IntentId
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEventType,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_store import StoredPaperMutationIntent
from trading_agent.paper_protective_oco_store import StoredProtectiveOcoPlan
from trading_agent.paper_safety_models import PaperSafetyPhase
from trading_agent.paper_safety_store import StoredPaperSafetyPlan
from trading_agent.us_equity_calendar import NEW_YORK


@dataclass(frozen=True, slots=True)
class PaperLifecycleProjection:
    state: Literal["empty", "populated", "blocked", "corrupt"]
    blocker_code: str | None
    items: tuple[WorkspaceItemV2, ...]


def project_paper_lifecycle(
    outputs: Path,
    snapshot: LaneDailySnapshot,
    *,
    bundled_ledger: ReconciliationLedger | None = None,
    bundled_identity: ExecutionLedgerSnapshotIdentity | None = None,
) -> PaperLifecycleProjection:
    path = outputs / "paper" / "execution.sqlite3"
    if not path.exists():
        return PaperLifecycleProjection("blocked", "paper_reconcile_pending", ())
    try:
        reader = ExecutionStore(path)
        if not reader.is_initialized():
            return PaperLifecycleProjection("corrupt", "paper_finalized_ledger_invalid", ())
        identity = reader.ledger_snapshot_identity() if bundled_identity is None else bundled_identity
        if identity.generation != snapshot.source_ledger_generation or identity.sha256 != snapshot.source_ledger_sha256:
            return PaperLifecycleProjection("corrupt", "paper_epoch_mismatch", ())
        ledger = reader.reconciliation_ledger() if bundled_ledger is None else bundled_ledger
        if any(state.anomaly_reasons for state in ledger.order_states):
            return PaperLifecycleProjection("corrupt", "paper_lifecycle_invalid", ())
        if (
            ledger.unresolved_intent_ids
            or ledger.pending_trade_update_receipt_keys
            or ledger.unrecovered_trade_update_quarantine_keys
        ):
            return PaperLifecycleProjection("blocked", "paper_reconcile_pending", ())
        intents = {intent.intent_id: intent for intent in ledger.intents}
        if len(intents) != len(ledger.intents) or any(
            _instant(intent.created_at) > snapshot.finalized_at for intent in ledger.intents
        ):
            return PaperLifecycleProjection("corrupt", "paper_lifecycle_invalid", ())
        plans = ledger.protective_oco_plans
        if any(
            plan.plan.parent_intent_id not in intents or _instant(plan.planned_at) > snapshot.finalized_at
            for plan in plans
        ):
            return PaperLifecycleProjection("corrupt", "paper_lifecycle_invalid", ())
        protected_ids = {plan.plan.parent_intent_id for plan in plans}
        if any(intent_id not in protected_ids for intent_id in ledger.filled_intent_ids):
            return PaperLifecycleProjection("blocked", "protective_oco_missing", ())
        if any(
            not protective_plan_terminal(plan, ledger, snapshot.finalized_at)
            for plan in plans
            if plan.plan.parent_intent_id in ledger.filled_intent_ids
        ):
            return PaperLifecycleProjection("blocked", "protective_oco_missing", ())
        recoveries = reader.paper_stream_recoveries()
        current_recoveries = tuple(
            recovery
            for recovery in recoveries
            if ledger.account_fingerprint == recovery.account_fingerprint
            and _instant(recovery.completed_at).astimezone(NEW_YORK).date() == snapshot.session_date
        )
        if not current_recoveries:
            return PaperLifecycleProjection("blocked", "paper_reconcile_pending", ())
        if any(
            not recovery.execution_detail_complete or _instant(recovery.completed_at) > snapshot.finalized_at
            for recovery in current_recoveries
        ):
            return PaperLifecycleProjection("corrupt", "paper_lifecycle_invalid", ())
        reconciled_at = max(_instant(recovery.completed_at) for recovery in current_recoveries)
        stage_items = (_stage_item("reconcile", "Final reconciliation", reconciled_at),)
        if snapshot.lane_id is LaneId.INTRADAY_MOMENTUM:
            cutoff_plans = _stage_plans(ledger, snapshot, PaperSafetyPhase.ENTRY_CUTOFF)
            if not cutoff_plans:
                return PaperLifecycleProjection("blocked", "paper_cutoff_pending", ())
            eod_plans = _stage_plans(ledger, snapshot, PaperSafetyPhase.EOD_FLATTEN)
            if not eod_plans:
                return PaperLifecycleProjection("blocked", "paper_eod_flat_pending", ())
            cutoff = max(cutoff_plans, key=lambda item: item.plan.observed_at)
            eod = max(eod_plans, key=lambda item: item.plan.observed_at)
            if (
                cutoff.plan.observed_at > snapshot.finalized_at
                or eod.plan.observed_at > snapshot.finalized_at
                or not reconciled_at <= cutoff.plan.observed_at <= eod.plan.observed_at
            ):
                return PaperLifecycleProjection("corrupt", "paper_lifecycle_invalid", ())
            if not _stage_terminal(cutoff, ledger, snapshot.finalized_at):
                return PaperLifecycleProjection("blocked", "paper_cutoff_pending", ())
            if not _stage_terminal(eod, ledger, snapshot.finalized_at):
                return PaperLifecycleProjection("blocked", "paper_eod_flat_pending", ())
            stage_items += (
                _stage_item("cutoff", "Entry cutoff", cutoff.plan.observed_at),
                _stage_item("eod_flat", "EOD flat", eod.plan.observed_at),
            )
        if reader.ledger_snapshot_identity() != identity:
            return PaperLifecycleProjection("corrupt", "paper_epoch_mismatch", ())
    except (OSError, RuntimeError, sqlite3.Error, ValueError):
        return PaperLifecycleProjection("corrupt", "paper_finalized_ledger_invalid", ())
    observed_at = snapshot.finalized_at
    source_id = "trace.paper.source"
    items = (
        WorkspaceItemV2(
            item_id="paper.lifecycle.entry",
            kind="paper",
            label="Finalized entry intents",
            state="empty" if not intents else "populated",
            value=f"{len(intents)} records",
            observed_at=observed_at,
            trace_id=source_id,
        ),
        WorkspaceItemV2(
            item_id="paper.lifecycle.protective_oco",
            kind="paper",
            label="Finalized protective OCO plans",
            state="empty" if not plans else "populated",
            value=f"{len(plans)} records",
            observed_at=observed_at,
            trace_id=source_id,
        ),
        *stage_items,
        *(
            WorkspaceItemV2(
                item_id=f"paper.order.{index}",
                kind="paper",
                label=intent.symbol,
                state="populated",
                value=_order_value(intent.intent_id, ledger.order_states),
                observed_at=observed_at,
                trace_id=source_id,
            )
            for index, intent in enumerate(ledger.intents[:8])
        ),
    )
    return PaperLifecycleProjection("populated", None, items)


def _stage_item(
    item_id: str,
    label: str,
    observed_at: dt.datetime,
) -> WorkspaceItemV2:
    return WorkspaceItemV2(
        item_id=f"paper.lifecycle.{item_id}",
        kind="paper",
        label=label,
        state="populated",
        value="finalized",
        observed_at=observed_at,
        trace_id="trace.paper.source",
    )


def _stage_plans(
    ledger: ReconciliationLedger,
    snapshot: LaneDailySnapshot,
    phase: PaperSafetyPhase,
) -> tuple[StoredPaperSafetyPlan, ...]:
    return tuple(
        plan
        for plan in ledger.paper_safety_plans
        if plan.plan.account_fingerprint == ledger.account_fingerprint
        and plan.plan.session_date == snapshot.session_date
        and plan.plan.phase is phase
    )


def _stage_terminal(
    plan: StoredPaperSafetyPlan,
    ledger: ReconciliationLedger,
    finalized_at: dt.datetime,
) -> bool:
    for sequence, _action in enumerate(plan.plan.actions):
        intents = tuple(
            stored
            for stored in ledger.paper_mutation_intents
            if stored.intent.safety_plan_key == plan.plan_key and stored.intent.action_sequence == sequence
        )
        if len(intents) != 1:
            return False
        intent = intents[0]
        if intent.intent.created_at < plan.plan.observed_at or not _mutation_terminal(
            intent,
            ledger,
            finalized_at,
        ):
            return False
    return True


def protective_plan_terminal(
    plan: StoredProtectiveOcoPlan,
    ledger: ReconciliationLedger,
    finalized_at: dt.datetime,
) -> bool:
    intents = tuple(
        stored
        for stored in ledger.paper_mutation_intents
        if stored.intent.protective_plan_key == plan.plan_key
        and stored.intent.operation is PaperMutationOperation.SUBMIT_PROTECTIVE_OCO
    )
    return (
        len(intents) == 1
        and intents[0].intent.created_at >= _instant(plan.planned_at)
        and _mutation_terminal(intents[0], ledger, finalized_at)
    )


def _mutation_terminal(
    intent: StoredPaperMutationIntent,
    ledger: ReconciliationLedger,
    finalized_at: dt.datetime,
) -> bool:
    events = tuple(
        stored.event for stored in ledger.paper_mutation_events if stored.mutation_key == intent.mutation_key
    )
    return (
        bool(events)
        and intent.intent.created_at <= finalized_at
        and events[-1].occurred_at >= intent.intent.created_at
        and events[-1].occurred_at <= finalized_at
        and events[-1].event_type
        in (
            PaperMutationEventType.ACKNOWLEDGED,
            PaperMutationEventType.RECOVERED_ACKNOWLEDGED,
        )
    )


def _order_value(
    intent_id: IntentId,
    states: tuple[BrokerOrderLedgerState, ...],
) -> str:
    state = next(candidate for candidate in states if candidate.intent_id == intent_id)
    terminal = state.terminal_event_types[-1].value if state.terminal_event_types else "no_fill"
    return f"entry · {terminal} · reconciled"


def _instant(value: str) -> dt.datetime:
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError
    return instant


__all__ = ("PaperLifecycleProjection", "project_paper_lifecycle", "protective_plan_terminal")
