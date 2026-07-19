from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_sip_dynamic_history_coverage import (
    verify_alpaca_sip_dynamic_history_as_of,
)
from trading_agent.alpaca_sip_dynamic_receipt_models import AlpacaSipDynamicTerminalStatus
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import AlpacaSipDynamicSubscriptionPlan
from trading_agent.alpaca_sip_dynamic_trade_state import _materialize_projected_trades_as_of
from trading_agent.alpaca_sip_dynamic_trade_state_models import AlpacaSipDynamicTradeState


class AlpacaSipDynamicTradeHistoryError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic trade history is invalid"


class AlpacaSipDynamicIncompleteTradeHistoryError(AlpacaSipDynamicTradeHistoryError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic trade history is incomplete"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicTradeHistory:
    state: AlpacaSipDynamicTradeState
    terminal_statuses: tuple[AlpacaSipDynamicTerminalStatus, ...]
    gap_count: int
    raw_first_verified: bool
    terminal_observed: bool
    continuity_attested: bool
    complete_history: bool
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.state) is not AlpacaSipDynamicTradeState
            or not self.terminal_statuses
            or any(type(item) is not AlpacaSipDynamicTerminalStatus for item in self.terminal_statuses)
            or len(self.terminal_statuses) != len(self.state.connection_epochs)
            or self.gap_count != len(self.terminal_statuses) - 1
            or type(self.raw_first_verified) is not bool
            or type(self.terminal_observed) is not bool
            or type(self.continuity_attested) is not bool
            or type(self.complete_history) is not bool
        ):
            raise AlpacaSipDynamicTradeHistoryError
        expected_continuity = (
            self.terminal_observed
            and len(self.terminal_statuses) == 1
            and (self.terminal_statuses[0] is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE)
        )
        expected_complete = self.raw_first_verified and expected_continuity
        expected_reasons = () if expected_complete else ("continuity_unattested",)
        if (
            self.continuity_attested is not expected_continuity
            or self.complete_history is not expected_complete
            or self.reason_codes != expected_reasons
        ):
            raise AlpacaSipDynamicTradeHistoryError


def materialize_alpaca_sip_dynamic_trade_history_as_of(
    store: AlpacaSipDynamicReceiptStore,
    plan: AlpacaSipDynamicSubscriptionPlan,
    *,
    as_of: dt.datetime,
) -> AlpacaSipDynamicTradeHistory:
    try:
        if type(store) is not AlpacaSipDynamicReceiptStore or type(plan) is not AlpacaSipDynamicSubscriptionPlan:
            raise AlpacaSipDynamicTradeHistoryError
        coverage = verify_alpaca_sip_dynamic_history_as_of(store, plan, as_of=as_of)
        state = _materialize_projected_trades_as_of(
            coverage.projected_messages,
            coverage.plan_id,
            coverage.market_date,
            coverage.connection_epochs,
            coverage.as_of,
        )
        return AlpacaSipDynamicTradeHistory(
            state,
            coverage.terminal_statuses,
            coverage.gap_count,
            coverage.raw_first_verified,
            coverage.terminal_observed,
            coverage.continuity_attested,
            coverage.complete_history,
            coverage.reason_codes,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipDynamicTradeHistoryError from None


def require_complete_alpaca_sip_dynamic_trade_history(
    history: AlpacaSipDynamicTradeHistory,
) -> AlpacaSipDynamicTradeHistory:
    if type(history) is not AlpacaSipDynamicTradeHistory:
        raise AlpacaSipDynamicTradeHistoryError
    if not history.complete_history:
        raise AlpacaSipDynamicIncompleteTradeHistoryError
    return history


__all__ = (
    "AlpacaSipDynamicIncompleteTradeHistoryError",
    "AlpacaSipDynamicTradeHistory",
    "AlpacaSipDynamicTradeHistoryError",
    "materialize_alpaca_sip_dynamic_trade_history_as_of",
    "require_complete_alpaca_sip_dynamic_trade_history",
)
