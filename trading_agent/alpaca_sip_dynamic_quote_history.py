from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_sip_dynamic_history_coverage import verify_alpaca_sip_dynamic_history_as_of
from trading_agent.alpaca_sip_dynamic_quote_state import _materialize_projected_quotes_as_of
from trading_agent.alpaca_sip_dynamic_quote_state_models import AlpacaSipDynamicQuoteState
from trading_agent.alpaca_sip_dynamic_receipt_models import AlpacaSipDynamicTerminalStatus
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import AlpacaSipDynamicSubscriptionPlan


class AlpacaSipDynamicQuoteHistoryError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic quote history is invalid"


class AlpacaSipDynamicIncompleteQuoteHistoryError(AlpacaSipDynamicQuoteHistoryError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic quote history is incomplete"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicQuoteHistory:
    state: AlpacaSipDynamicQuoteState
    terminal_statuses: tuple[AlpacaSipDynamicTerminalStatus, ...]
    gap_count: int
    raw_first_verified: bool
    terminal_observed: bool
    continuity_attested: bool
    complete_history: bool
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        expected_continuity = (
            self.terminal_observed
            and len(self.terminal_statuses) == 1
            and self.terminal_statuses[0] is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE
        )
        expected_complete = self.raw_first_verified and expected_continuity
        expected_reasons = () if expected_complete else ("continuity_unattested",)
        if (
            type(self.state) is not AlpacaSipDynamicQuoteState
            or not self.terminal_statuses
            or any(type(item) is not AlpacaSipDynamicTerminalStatus for item in self.terminal_statuses)
            or len(self.terminal_statuses) != len(self.state.connection_epochs)
            or self.gap_count != len(self.terminal_statuses) - 1
            or self.raw_first_verified is not True
            or type(self.terminal_observed) is not bool
            or self.continuity_attested is not expected_continuity
            or self.complete_history is not expected_complete
            or self.reason_codes != expected_reasons
        ):
            raise AlpacaSipDynamicQuoteHistoryError


def materialize_alpaca_sip_dynamic_quote_history_as_of(
    store: AlpacaSipDynamicReceiptStore,
    plan: AlpacaSipDynamicSubscriptionPlan,
    *,
    as_of: dt.datetime,
) -> AlpacaSipDynamicQuoteHistory:
    try:
        coverage = verify_alpaca_sip_dynamic_history_as_of(store, plan, as_of=as_of)
        state = _materialize_projected_quotes_as_of(
            coverage.projected_messages,
            coverage.plan_id,
            coverage.market_date,
            coverage.connection_epochs,
            coverage.as_of,
        )
        return AlpacaSipDynamicQuoteHistory(
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
        raise AlpacaSipDynamicQuoteHistoryError from None


def require_complete_alpaca_sip_dynamic_quote_history(
    history: AlpacaSipDynamicQuoteHistory,
) -> AlpacaSipDynamicQuoteHistory:
    if type(history) is not AlpacaSipDynamicQuoteHistory:
        raise AlpacaSipDynamicQuoteHistoryError
    if not history.complete_history:
        raise AlpacaSipDynamicIncompleteQuoteHistoryError
    return history


__all__ = (
    "AlpacaSipDynamicIncompleteQuoteHistoryError",
    "AlpacaSipDynamicQuoteHistory",
    "AlpacaSipDynamicQuoteHistoryError",
    "materialize_alpaca_sip_dynamic_quote_history_as_of",
    "require_complete_alpaca_sip_dynamic_quote_history",
)
