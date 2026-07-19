from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_sip_dynamic_market_models import AlpacaSipProjectedMarketMessage
from trading_agent.alpaca_sip_dynamic_projection import project_alpaca_sip_dynamic_receipts
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicReceiptKind,
    AlpacaSipDynamicTerminalStatus,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import AlpacaSipDynamicSubscriptionPlan
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore


class AlpacaSipDynamicHistoryCoverageError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic history coverage is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicHistoryCoverage:
    plan_id: str
    connection_epochs: tuple[str, ...]
    market_date: dt.date
    as_of: dt.datetime
    projected_messages: tuple[AlpacaSipProjectedMarketMessage, ...]
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
            len(self.plan_id) != 64
            or not self.connection_epochs
            or any(len(epoch) != 32 for epoch in self.connection_epochs)
            or self.connection_epochs != tuple(dict.fromkeys(self.connection_epochs))
            or type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or not _aware(self.as_of)
            or any(type(item) is not AlpacaSipProjectedMarketMessage for item in self.projected_messages)
            or not self.terminal_statuses
            or any(type(item) is not AlpacaSipDynamicTerminalStatus for item in self.terminal_statuses)
            or len(self.terminal_statuses) != len(self.connection_epochs)
            or self.gap_count != len(self.connection_epochs) - 1
            or self.raw_first_verified is not True
            or type(self.terminal_observed) is not bool
            or self.continuity_attested is not expected_continuity
            or self.complete_history is not expected_complete
            or self.reason_codes != expected_reasons
        ):
            raise AlpacaSipDynamicHistoryCoverageError


def verify_alpaca_sip_dynamic_history_as_of(
    store: AlpacaSipDynamicReceiptStore,
    plan: AlpacaSipDynamicSubscriptionPlan,
    *,
    as_of: dt.datetime,
) -> AlpacaSipDynamicHistoryCoverage:
    try:
        if (
            type(store) is not AlpacaSipDynamicReceiptStore
            or type(plan) is not AlpacaSipDynamicSubscriptionPlan
            or not _aware(as_of)
        ):
            raise AlpacaSipDynamicHistoryCoverageError
        terminals = AlpacaSipDynamicTerminalStore(store.path).load_history(plan)
        if not terminals:
            raise AlpacaSipDynamicHistoryCoverageError
        completed = tuple(item for item in terminals if item.status is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE)
        last_terminal = terminals[len(terminals) - 1]
        if len(terminals) > 10 or len(completed) > 1 or (completed and last_terminal != completed[0]):
            raise AlpacaSipDynamicHistoryCoverageError
        projected: list[AlpacaSipProjectedMarketMessage] = []
        previous_terminal_at: dt.datetime | None = None
        for terminal in terminals:
            replay = store.load_replay(plan, terminal.connection_epoch)
            if previous_terminal_at is not None and replay and replay[0].received_at < previous_terminal_at:
                raise AlpacaSipDynamicHistoryCoverageError
            has_data = any(item.kind is AlpacaSipDynamicReceiptKind.DATA for item in replay)
            if has_data:
                if len(replay) < 4:
                    raise AlpacaSipDynamicHistoryCoverageError
                projected.extend(project_alpaca_sip_dynamic_receipts(store, plan, terminal.connection_epoch))
            elif len(replay) > 3:
                raise AlpacaSipDynamicHistoryCoverageError
            previous_terminal_at = terminal.terminal_at
        statuses = tuple(item.status for item in terminals)
        terminal_observed = all(item.terminal_at <= as_of for item in terminals)
        continuity = (
            terminal_observed and len(statuses) == 1 and statuses[0] is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE
        )
        return AlpacaSipDynamicHistoryCoverage(
            plan.plan_id,
            tuple(item.connection_epoch for item in terminals),
            plan.market_date,
            as_of.astimezone(dt.UTC),
            tuple(projected),
            statuses,
            len(statuses) - 1,
            True,
            terminal_observed,
            continuity,
            continuity,
            () if continuity else ("continuity_unattested",),
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipDynamicHistoryCoverageError from None


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicHistoryCoverage",
    "AlpacaSipDynamicHistoryCoverageError",
    "verify_alpaca_sip_dynamic_history_as_of",
)
