from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.paper_execution_models import BrokerOrderId, IntentId, PaperBrokerState
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEventType,
    PaperMutationOperation,
)
from trading_agent.paper_protective_exit import (
    BlockedProtectiveExitPlan,
    NoProtectiveExitRequired,
    ProtectiveExitPlanDecision,
    plan_protective_oco_exit,
    protective_oco_covers_position,
)
from trading_agent.paper_protective_oco_models import ProtectiveOcoSnapshot
from trading_agent.paper_protective_oco_store import (
    ProtectiveOcoPlanKey,
    StoredProtectiveOcoPlan,
    protective_oco_snapshot_matches_plan,
)

_CANCELABLE_STATUSES = frozenset({"new", "accepted", "pending_new", "partially_filled"})
_TERMINAL_STATUSES = frozenset(
    {
        "filled",
        "canceled",
        "expired",
        "rejected",
        "done_for_day",
        "stopped",
        "calculated",
        "replaced",
    }
)


@dataclass(frozen=True, slots=True)
class ProtectiveOcoResizeCancelPlan:
    parent_intent_id: IntentId
    source_plan_key: ProtectiveOcoPlanKey
    broker_order_id: BrokerOrderId
    symbol: str
    observed_at: dt.datetime


type ProtectiveOcoLifecycleDecision = ProtectiveExitPlanDecision | ProtectiveOcoResizeCancelPlan


def plan_current_protective_oco_lifecycle(
    ledger: ReconciliationLedger,
    broker_state: PaperBrokerState,
    observed_protective_ocos: tuple[ProtectiveOcoSnapshot, ...],
    parent_intent_id: IntentId,
) -> ProtectiveOcoLifecycleDecision:
    intents = tuple(intent for intent in ledger.intents if intent.intent_id == parent_intent_id)
    order_states = tuple(state for state in ledger.order_states if state.intent_id == parent_intent_id)
    if len(intents) != 1 or len(order_states) != 1:
        return _blocked("보호 대상 current-epoch intent와 체결 원장이 유일하지 않습니다")
    intent = intents[0]
    plans = tuple(plan for plan in ledger.protective_oco_plans if plan.plan.parent_intent_id == parent_intent_id)
    relevant_history = tuple(
        snapshot for snapshot in observed_protective_ocos if snapshot.take_profit.symbol == intent.symbol
    )
    matched, reasons = _matched_history(plans, relevant_history)
    if reasons:
        return BlockedProtectiveExitPlan(reasons)
    protective_fill, fill_reasons = _confirmed_protective_fill(relevant_history)
    if fill_reasons:
        return BlockedProtectiveExitPlan(fill_reasons)
    positions = tuple(position for position in broker_state.positions if position.symbol == intent.symbol)
    if len(positions) > 1:
        return _blocked("보호 대상 current-epoch broker 포지션이 중복됩니다")
    position = None if not positions else positions[0]
    open_ocos = tuple(
        snapshot for snapshot in broker_state.protective_ocos if snapshot.take_profit.symbol == intent.symbol
    )
    if len(open_ocos) > 1:
        return _blocked("보호 대상 current-epoch broker OCO가 중복됩니다")
    open_source: StoredProtectiveOcoPlan | None = None
    if open_ocos:
        sources = _matching_plans(plans, open_ocos[0])
        if len(sources) != 1:
            return _blocked("열린 보호 OCO와 immutable 계획이 유일하게 일치하지 않습니다")
        open_source = sources[0]
    latest_source = _latest_matched_plan(plans, matched)
    if (
        not open_ocos
        and not relevant_history
        and _prior_protective_mutation_requires_history(
            ledger,
            frozenset(plan.plan_key for plan in plans),
        )
    ):
        return _blocked("기존 보호 OCO mutation의 current history가 없습니다")
    if not open_ocos and relevant_history and any(not _snapshot_is_terminal(snapshot) for snapshot in relevant_history):
        return _blocked("열린 목록에서 사라진 보호 OCO의 terminal 상태가 확인되지 않았습니다")
    desired = plan_protective_oco_exit(
        intent,
        order_states[0],
        position,
        confirmed_protective_fill=protective_fill,
        replacement_source_key=(None if latest_source is None or open_ocos else latest_source.plan_key),
    )
    if isinstance(desired, BlockedProtectiveExitPlan):
        return desired
    if not open_ocos:
        return desired
    snapshot = open_ocos[0]
    if open_source is None:
        return _blocked("열린 보호 OCO의 source 계획을 확인하지 못했습니다")
    if position is not None and protective_oco_covers_position(open_source.plan, snapshot, position):
        return NoProtectiveExitRequired(parent_intent_id)
    statuses = (snapshot.take_profit.status, snapshot.stop_loss.status)
    if "pending_cancel" in statuses:
        return _blocked("보호 OCO가 pending_cancel이므로 terminal 대사를 기다립니다")
    if not all(status in _CANCELABLE_STATUSES for status in statuses):
        return _blocked("보호 OCO가 current-epoch cancel 가능한 상태가 아닙니다")
    return ProtectiveOcoResizeCancelPlan(
        parent_intent_id,
        open_source.plan_key,
        snapshot.take_profit.broker_order_id,
        intent.symbol,
        snapshot.observed_at,
    )


def _matched_history(
    plans: tuple[StoredProtectiveOcoPlan, ...],
    snapshots: tuple[ProtectiveOcoSnapshot, ...],
) -> tuple[tuple[tuple[StoredProtectiveOcoPlan, ProtectiveOcoSnapshot], ...], tuple[str, ...]]:
    matches: list[tuple[StoredProtectiveOcoPlan, ProtectiveOcoSnapshot]] = []
    reasons: list[str] = []
    parent_ids = tuple(snapshot.take_profit.broker_order_id for snapshot in snapshots)
    client_ids = tuple(snapshot.take_profit.client_order_id for snapshot in snapshots)
    if len(parent_ids) != len(set(parent_ids)) or len(client_ids) != len(set(client_ids)):
        reasons.append("보호 OCO history identity가 중복됩니다")
    for snapshot in snapshots:
        candidates = _matching_plans(plans, snapshot)
        if len(candidates) != 1:
            reasons.append("보호 OCO history와 immutable 계획이 유일하게 일치하지 않습니다")
        else:
            matches.append((candidates[0], snapshot))
    return tuple(matches), tuple(dict.fromkeys(reasons))


def _matching_plans(
    plans: tuple[StoredProtectiveOcoPlan, ...],
    snapshot: ProtectiveOcoSnapshot,
) -> tuple[StoredProtectiveOcoPlan, ...]:
    return tuple(plan for plan in plans if protective_oco_snapshot_matches_plan(snapshot, plan.plan))


def _latest_matched_plan(
    plans: tuple[StoredProtectiveOcoPlan, ...],
    matched: tuple[tuple[StoredProtectiveOcoPlan, ProtectiveOcoSnapshot], ...],
) -> StoredProtectiveOcoPlan | None:
    keys = frozenset(plan.plan_key for plan, _ in matched)
    return next((plan for plan in reversed(plans) if plan.plan_key in keys), None)


def _confirmed_protective_fill(
    snapshots: tuple[ProtectiveOcoSnapshot, ...],
) -> tuple[Decimal, tuple[str, ...]]:
    total = Decimal(0)
    reasons: list[str] = []
    for snapshot in snapshots:
        take_profit = snapshot.take_profit.filled_quantity
        stop_loss = snapshot.stop_loss.filled_quantity
        if (
            not take_profit.is_finite()
            or not stop_loss.is_finite()
            or take_profit < 0
            or stop_loss < 0
            or take_profit > snapshot.take_profit.quantity
            or stop_loss > snapshot.stop_loss.quantity
        ):
            reasons.append("보호 OCO leg 체결 수량이 유효하지 않습니다")
            continue
        if take_profit > 0 and stop_loss > 0:
            reasons.append("보호 OCO 양쪽 leg 체결 경합은 자동 복구하지 않습니다")
            continue
        total += take_profit + stop_loss
    return total, tuple(dict.fromkeys(reasons))


def _snapshot_is_terminal(snapshot: ProtectiveOcoSnapshot) -> bool:
    return snapshot.take_profit.status in _TERMINAL_STATUSES and snapshot.stop_loss.status in _TERMINAL_STATUSES


def _prior_protective_mutation_requires_history(
    ledger: ReconciliationLedger,
    plan_keys: frozenset[ProtectiveOcoPlanKey],
) -> bool:
    for stored_intent in ledger.paper_mutation_intents:
        intent = stored_intent.intent
        if (
            intent.operation is not PaperMutationOperation.SUBMIT_PROTECTIVE_OCO
            or intent.protective_plan_key not in plan_keys
        ):
            continue
        events = tuple(
            stored_event
            for stored_event in ledger.paper_mutation_events
            if stored_event.mutation_key == stored_intent.mutation_key
        )
        latest_event_type = (
            max(events, key=lambda stored_event: stored_event.event_id).event.event_type if events else None
        )
        if latest_event_type is not None and latest_event_type is not PaperMutationEventType.RECOVERED_ABSENT:
            return True
    return False


def _blocked(reason: str) -> BlockedProtectiveExitPlan:
    return BlockedProtectiveExitPlan((reason,))
