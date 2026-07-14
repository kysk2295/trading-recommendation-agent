from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperOrderSide,
)


class PaperMutationOperation(StrEnum):
    SUBMIT_ENTRY = "submit_entry"
    SUBMIT_PROTECTIVE_OCO = "submit_protective_oco"
    CANCEL_ORDER = "cancel_order"
    CLOSE_POSITION = "close_position"


class PaperMutationEventType(StrEnum):
    ATTEMPTED = "attempted"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"
    RECOVERED_ACKNOWLEDGED = "recovered_acknowledged"
    RECOVERED_ABSENT = "recovered_absent"


@dataclass(frozen=True, slots=True)
class PaperMutationIntent:
    account_fingerprint: AccountFingerprint
    created_at: dt.datetime
    operation: PaperMutationOperation
    protective_plan_key: str | None
    safety_plan_key: str | None
    action_sequence: int | None
    request_sha256: str
    symbol: str
    broker_order_id: BrokerOrderId | None
    side: PaperOrderSide | None
    quantity: Decimal | None
    entry_intent_id: IntentId | None = None


@dataclass(frozen=True, slots=True)
class PaperMutationEvent:
    attempt_number: int
    occurred_at: dt.datetime
    event_type: PaperMutationEventType
    request_id: str | None
    status_code: int | None
    broker_order_id: BrokerOrderId | None
    evidence_sha256: str
