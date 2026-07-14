from __future__ import annotations

from dataclasses import dataclass

from trading_agent.execution_schema import StoredBrokerEvent
from trading_agent.paper_account_activity_store import StoredPaperAccountActivity
from trading_agent.paper_stream_recovery import StoredPaperRecoveryOrder
from trading_agent.trade_update_schema import StoredTradeUpdate


@dataclass(frozen=True, slots=True)
class BrokerOrderEvidence:
    broker_events: tuple[StoredBrokerEvent, ...]
    trade_updates: tuple[StoredTradeUpdate, ...]
    recovery_orders: tuple[StoredPaperRecoveryOrder, ...]
    account_activities: tuple[StoredPaperAccountActivity, ...]
