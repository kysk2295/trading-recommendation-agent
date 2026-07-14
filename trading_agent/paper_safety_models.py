from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    PaperOrderSide,
)


class PaperSafetyPhase(StrEnum):
    MONITORING = "monitoring"
    ENTRY_CUTOFF = "entry_cutoff"
    KILL_SWITCH = "kill_switch"
    EOD_FLATTEN = "eod_flatten"


@dataclass(frozen=True, slots=True)
class PaperCancelOrderAction:
    broker_order_id: BrokerOrderId
    symbol: str
    protective_oco: bool
    kind: Literal["cancel_order"] = field(init=False, default="cancel_order")


@dataclass(frozen=True, slots=True)
class PaperClosePositionAction:
    symbol: str
    side: PaperOrderSide
    quantity: Decimal
    kind: Literal["close_position"] = field(init=False, default="close_position")


type PaperSafetyAction = PaperCancelOrderAction | PaperClosePositionAction


@dataclass(frozen=True, slots=True)
class PaperSafetyPlan:
    account_fingerprint: AccountFingerprint
    observed_at: dt.datetime
    session_date: dt.date
    phase: PaperSafetyPhase
    mark_to_market_daily_pnl: Decimal
    conservative_daily_pnl: Decimal
    actions: tuple[PaperSafetyAction, ...]


@dataclass(frozen=True, slots=True)
class BlockedPaperSafetyPlan:
    reasons: tuple[str, ...]


type PaperSafetyPlanDecision = PaperSafetyPlan | BlockedPaperSafetyPlan
