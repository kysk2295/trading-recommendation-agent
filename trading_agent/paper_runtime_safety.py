from __future__ import annotations

import datetime as dt

from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.paper_execution_models import PaperBrokerState
from trading_agent.paper_order_gate_models import CompletePaperPortfolio
from trading_agent.paper_risk import PaperRiskConfig
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.paper_safety_latch import (
    DAILY_KILL_SWITCH_LATCHED,
    daily_kill_switch_latched,
)
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperSafetyPhase,
    PaperSafetyPlan,
    PaperSafetyPlanDecision,
)
from trading_agent.paper_safety_planner import plan_paper_safety_actions
from trading_agent.us_equity_calendar import NEW_YORK


def daily_kill_latch_reasons(
    ledger: ReconciliationLedger,
    broker_state: PaperBrokerState,
    evaluated_at: dt.datetime,
) -> tuple[str, ...]:
    if daily_kill_switch_latched(
        ledger.paper_safety_plans,
        broker_state.account.account_fingerprint,
        evaluated_at.astimezone(NEW_YORK).date(),
    ):
        return (DAILY_KILL_SWITCH_LATCHED,)
    return ()


def plan_runtime_safety(
    readiness: PaperRuntimeReadiness,
    evaluated_at: dt.datetime,
    config: PaperRiskConfig,
) -> PaperSafetyPlanDecision:
    kill_latched = DAILY_KILL_SWITCH_LATCHED in readiness.runtime_reasons
    reasons = tuple(reason for reason in readiness.runtime_reasons if reason != DAILY_KILL_SWITCH_LATCHED)
    if not readiness.reconciliation.ready:
        reasons = (*reasons, *readiness.reconciliation.reasons)
    if not isinstance(readiness.portfolio, CompletePaperPortfolio):
        reasons = (*reasons, *readiness.portfolio.reasons)
    if reasons:
        return BlockedPaperSafetyPlan(tuple(sorted(set(reasons))))
    decision = plan_paper_safety_actions(
        readiness.broker_state,
        readiness.market_clock,
        readiness.portfolio,
        evaluated_at,
        config,
        kill_switch_latched=kill_latched,
    )
    if (
        isinstance(decision, PaperSafetyPlan)
        and decision.phase in (PaperSafetyPhase.MONITORING, PaperSafetyPhase.ENTRY_CUTOFF)
        and readiness.protective_exit_reasons
    ):
        return BlockedPaperSafetyPlan(readiness.protective_exit_reasons)
    return decision
