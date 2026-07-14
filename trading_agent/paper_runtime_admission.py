from __future__ import annotations

import datetime as dt

from trading_agent.paper_execution_models import PaperOrderIntent
from trading_agent.paper_order_gate import _evaluate_reconciled_paper_order_gate
from trading_agent.paper_order_gate_models import (
    BlockedPaperOrderGateDecision,
    CompletePaperPortfolio,
    LatestCompletedBar,
    PaperOrderGateDecision,
    PaperOrderGateSnapshot,
    PaperOrderGateState,
)
from trading_agent.paper_risk import PaperRiskConfig
from trading_agent.paper_runtime import PaperRuntimeReadiness


def evaluate_runtime_order(
    readiness: PaperRuntimeReadiness,
    latest_bar: LatestCompletedBar,
    candidate_intent: PaperOrderIntent,
    liquidity_allowed_quantity: int,
    estimated_spread_bps: float,
    config: PaperRiskConfig,
    evaluated_at: dt.datetime,
) -> PaperOrderGateDecision:
    if readiness.runtime_reasons:
        return BlockedPaperOrderGateDecision(
            PaperOrderGateState.RECONCILIATION_BLOCKED,
            readiness.runtime_reasons,
        )
    if not readiness.reconciliation.ready:
        return BlockedPaperOrderGateDecision(
            PaperOrderGateState.RECONCILIATION_BLOCKED,
            readiness.reconciliation.reasons,
        )
    if not isinstance(readiness.portfolio, CompletePaperPortfolio):
        return BlockedPaperOrderGateDecision(
            PaperOrderGateState.PORTFOLIO_BLOCKED,
            readiness.portfolio.reasons,
        )
    if readiness.protective_exit_reasons:
        return BlockedPaperOrderGateDecision(
            PaperOrderGateState.PORTFOLIO_BLOCKED,
            readiness.protective_exit_reasons,
        )
    snapshot = PaperOrderGateSnapshot(
        market_clock=readiness.market_clock,
        latest_bar=latest_bar,
        stream_heartbeat=readiness.stream_heartbeat,
        portfolio=readiness.portfolio,
        candidate_intent=candidate_intent,
        liquidity_allowed_quantity=liquidity_allowed_quantity,
        estimated_spread_bps=estimated_spread_bps,
    )
    return _evaluate_reconciled_paper_order_gate(snapshot, evaluated_at, config)
