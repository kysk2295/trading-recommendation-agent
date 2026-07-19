from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_sip_dynamic_projection import project_alpaca_sip_dynamic_receipts
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicReceiptKind,
    AlpacaSipDynamicTerminalStatus,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import AlpacaSipDynamicSubscriptionPlan
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore
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
        terminals = AlpacaSipDynamicTerminalStore(store.path).load_history(plan)
        if not terminals:
            raise AlpacaSipDynamicTradeHistoryError
        completed = tuple(item for item in terminals if item.status is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE)
        last_terminal = terminals[len(terminals) - 1]
        if len(terminals) > 10 or len(completed) > 1 or (completed and last_terminal != completed[0]):
            raise AlpacaSipDynamicTradeHistoryError
        projected = []
        previous_terminal_at: dt.datetime | None = None
        for terminal in terminals:
            replay = store.load_replay(plan, terminal.connection_epoch)
            if previous_terminal_at is not None and replay and replay[0].received_at < previous_terminal_at:
                raise AlpacaSipDynamicTradeHistoryError
            has_data = any(item.kind is AlpacaSipDynamicReceiptKind.DATA for item in replay)
            if has_data:
                if len(replay) < 4:
                    raise AlpacaSipDynamicTradeHistoryError
                projected.extend(project_alpaca_sip_dynamic_receipts(store, plan, terminal.connection_epoch))
            elif len(replay) > 3:
                raise AlpacaSipDynamicTradeHistoryError
            previous_terminal_at = terminal.terminal_at
        state = _materialize_projected_trades_as_of(
            tuple(projected),
            plan.plan_id,
            plan.market_date,
            tuple(item.connection_epoch for item in terminals),
            as_of,
        )
        statuses = tuple(item.status for item in terminals)
        terminal_observed = all(item.terminal_at <= as_of for item in terminals)
        continuity = (
            terminal_observed and len(statuses) == 1 and statuses[0] is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE
        )
        return AlpacaSipDynamicTradeHistory(
            state,
            statuses,
            len(statuses) - 1,
            True,
            terminal_observed,
            continuity,
            continuity,
            () if continuity else ("continuity_unattested",),
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
