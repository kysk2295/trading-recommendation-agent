from __future__ import annotations

from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.paper_execution_models import IntentId, PaperBrokerState
from trading_agent.paper_protective_exit import (
    BlockedProtectiveExitPlan,
    ProtectiveExitPlanDecision,
    plan_protective_oco_exit,
)


def plan_current_protective_exit(
    ledger: ReconciliationLedger,
    broker_state: PaperBrokerState,
    parent_intent_id: IntentId,
) -> ProtectiveExitPlanDecision:
    intents = tuple(intent for intent in ledger.intents if intent.intent_id == parent_intent_id)
    order_states = tuple(state for state in ledger.order_states if state.intent_id == parent_intent_id)
    if len(intents) != 1 or len(order_states) != 1:
        return BlockedProtectiveExitPlan(("보호 대상 current-epoch intent와 체결 원장이 유일하지 않습니다",))
    positions = tuple(position for position in broker_state.positions if position.symbol == intents[0].symbol)
    if len(positions) > 1:
        return BlockedProtectiveExitPlan(("보호 대상 current-epoch broker 포지션이 중복됩니다",))
    position = None if not positions else positions[0]
    return plan_protective_oco_exit(intents[0], order_states[0], position)
