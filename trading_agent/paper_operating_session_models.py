from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, override

from trading_agent.paper_execution_models import PaperOrderIntent
from trading_agent.paper_order_gate_models import LatestCompletedBar, PaperOrderGateDecision
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_safety_models import PaperSafetyPlanDecision
from trading_agent.paper_trade_update_classification import PaperTradeUpdateIngestionResult


@dataclass(frozen=True, slots=True)
class PaperOrderAdmissionRequest:
    latest_bar: LatestCompletedBar
    candidate_intent: PaperOrderIntent
    liquidity_allowed_quantity: int
    estimated_spread_bps: float
    config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG


class PaperOperatingSession(Protocol):
    def ingest_next(self, timeout_seconds: float) -> PaperTradeUpdateIngestionResult: ...

    def evaluate_order(
        self,
        request: PaperOrderAdmissionRequest,
    ) -> PaperOrderGateDecision: ...

    def plan_safety_actions(
        self,
        config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    ) -> PaperSafetyPlanDecision: ...


class InactivePaperOperatingSessionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper 단일 운영 세션이 이미 종료되었습니다"


class BusyPaperOperatingSessionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca Paper 단일 운영 세션에서 다른 연산이 진행 중입니다"
