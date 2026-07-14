from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import assert_never

from trading_agent.paper_execution_models import BrokerOrderId
from trading_agent.paper_mutation_ledger_models import PaperMutationEvent, PaperMutationEventType
from trading_agent.paper_mutation_models import (
    PaperCancelOrderReceipt,
    PaperClosePositionReceipt,
    PaperEntryOrderReceipt,
    PaperProtectiveOcoReceipt,
)

type PaperMutationReceipt = (
    PaperProtectiveOcoReceipt | PaperCancelOrderReceipt | PaperClosePositionReceipt | PaperEntryOrderReceipt
)


def acknowledged_mutation_event(
    receipt: PaperMutationReceipt,
    attempt_number: int,
    occurred_at: dt.datetime,
) -> PaperMutationEvent:
    match receipt:
        case PaperEntryOrderReceipt():
            status_code = 200
            broker_order_id = receipt.order.broker_order_id
        case PaperProtectiveOcoReceipt():
            status_code = 200
            broker_order_id = receipt.snapshot.take_profit.broker_order_id
        case PaperCancelOrderReceipt():
            status_code = 204
            broker_order_id = receipt.broker_order_id
        case PaperClosePositionReceipt():
            status_code = 200
            broker_order_id = receipt.order.broker_order_id
        case unreachable:
            assert_never(unreachable)
    evidence = json.dumps(
        ("acknowledged", receipt.request_id, status_code, broker_order_id),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return PaperMutationEvent(
        attempt_number,
        occurred_at,
        PaperMutationEventType.ACKNOWLEDGED,
        receipt.request_id,
        status_code,
        BrokerOrderId(broker_order_id),
        hashlib.sha256(evidence.encode()).hexdigest(),
    )
