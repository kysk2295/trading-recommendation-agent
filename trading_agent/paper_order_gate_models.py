from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import override

from trading_agent.alpaca_paper_order_stream import PaperOrderStreamHeartbeat
from trading_agent.paper_execution_models import (
    IntentId,
    PaperMarketClockSnapshot,
    PaperOrderIntent,
    SizedPaperOrder,
)


class PaperOrderGateState(StrEnum):
    SESSION_BLOCKED = "session_blocked"
    CURRENT_BAR_BLOCKED = "current_bar_blocked"
    STREAM_BLOCKED = "stream_blocked"
    RECONCILIATION_BLOCKED = "reconciliation_blocked"
    PORTFOLIO_BLOCKED = "portfolio_blocked"
    APPROVED = "approved"


class PaperExposureKind(StrEnum):
    PENDING_ENTRY = "pending_entry"
    PARTIAL_ENTRY = "partial_entry"
    OPEN_POSITION = "open_position"


@dataclass(frozen=True, slots=True)
class LatestCompletedBar:
    symbol: str
    started_at: dt.datetime
    first_observed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class PaperPortfolioExposure:
    intent_id: IntentId
    symbol: str
    kind: PaperExposureKind
    gross_exposure: Decimal
    planned_risk: Decimal


@dataclass(frozen=True, slots=True)
class CompletePaperPortfolio:
    observed_at: dt.datetime
    account_status: str
    trading_blocked: bool
    equity: Decimal
    last_equity: Decimal
    buying_power: Decimal
    exposures: tuple[PaperPortfolioExposure, ...]

    @property
    def exposed_symbols(self) -> frozenset[str]:
        return frozenset(exposure.symbol for exposure in self.exposures)

    @property
    def gross_exposure(self) -> Decimal:
        return sum(
            (exposure.gross_exposure for exposure in self.exposures),
            start=Decimal(0),
        )

    @property
    def planned_open_risk(self) -> Decimal:
        return sum(
            (exposure.planned_risk for exposure in self.exposures),
            start=Decimal(0),
        )


@dataclass(frozen=True, slots=True)
class IncompletePaperPortfolio:
    reasons: tuple[str, ...]


type PaperPortfolioSnapshot = CompletePaperPortfolio | IncompletePaperPortfolio


class InvalidPaperOrderGateDecisionError(ValueError):
    @override
    def __str__(self) -> str:
        return "차단 결정에는 차단 상태와 사유가 필요합니다"


@dataclass(frozen=True, slots=True)
class PaperOrderGateSnapshot:
    market_clock: PaperMarketClockSnapshot
    latest_bar: LatestCompletedBar
    stream_heartbeat: PaperOrderStreamHeartbeat
    portfolio: PaperPortfolioSnapshot
    candidate_intent: PaperOrderIntent
    liquidity_allowed_quantity: int
    estimated_spread_bps: float


@dataclass(frozen=True, slots=True)
class ApprovedPaperOrderGateDecision:
    sized_order: SizedPaperOrder
    state: PaperOrderGateState = field(
        init=False,
        default=PaperOrderGateState.APPROVED,
    )
    reasons: tuple[str, ...] = field(init=False, default=())


@dataclass(frozen=True, slots=True)
class BlockedPaperOrderGateDecision:
    state: PaperOrderGateState
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state is PaperOrderGateState.APPROVED or not self.reasons:
            raise InvalidPaperOrderGateDecisionError


type PaperOrderGateDecision = (
    ApprovedPaperOrderGateDecision | BlockedPaperOrderGateDecision
)
