from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.dashboard_models_v2 import WorkspaceItemV2
from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.paper_execution_models import IntentId


@dataclass(frozen=True, slots=True)
class PaperLifecycleProjection:
    state: Literal["empty", "populated", "blocked", "corrupt"]
    blocker_code: str | None
    items: tuple[WorkspaceItemV2, ...]


class InvalidPaperLifecycleTimestampError(ValueError):
    pass


def project_paper_lifecycle(
    outputs: Path,
    snapshot: LaneDailySnapshot,
) -> PaperLifecycleProjection:
    path = outputs / "paper" / "execution.sqlite3"
    if not path.exists():
        return PaperLifecycleProjection("empty", None, ())
    try:
        reader = ExecutionStore(path)
        if not reader.is_initialized():
            return PaperLifecycleProjection("corrupt", "paper_finalized_ledger_invalid", ())
        identity = reader.ledger_snapshot_identity()
        if identity.generation != snapshot.source_ledger_generation or identity.sha256 != snapshot.source_ledger_sha256:
            return PaperLifecycleProjection("corrupt", "paper_epoch_mismatch", ())
        ledger = reader.reconciliation_ledger()
        if reader.ledger_snapshot_identity() != identity:
            return PaperLifecycleProjection("corrupt", "paper_epoch_mismatch", ())
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
            plan.plan.session_date != snapshot.session_date or plan.plan.observed_at > snapshot.finalized_at
            for plan in ledger.paper_safety_plans
        ):
            return PaperLifecycleProjection("corrupt", "paper_lifecycle_invalid", ())
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
    return PaperLifecycleProjection("populated" if intents or plans else "empty", None, items)


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
        raise InvalidPaperLifecycleTimestampError
    return instant


__all__ = ("PaperLifecycleProjection", "project_paper_lifecycle")
