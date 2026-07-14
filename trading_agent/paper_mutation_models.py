from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import NewType

from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSnapshot
from trading_agent.paper_protective_oco_models import ProtectiveOcoSnapshot

PaperMutationRequestId = NewType("PaperMutationRequestId", str)


@dataclass(frozen=True, slots=True)
class PaperProtectiveOcoReceipt:
    request_id: PaperMutationRequestId
    snapshot: ProtectiveOcoSnapshot


@dataclass(frozen=True, slots=True)
class PaperCancelOrderReceipt:
    request_id: PaperMutationRequestId
    broker_order_id: BrokerOrderId
    accepted_at: dt.datetime


@dataclass(frozen=True, slots=True)
class PaperClosePositionReceipt:
    request_id: PaperMutationRequestId
    received_at: dt.datetime
    order: PaperOrderSnapshot


@dataclass(frozen=True, slots=True)
class PaperEntryOrderReceipt:
    request_id: PaperMutationRequestId
    received_at: dt.datetime
    order: PaperOrderSnapshot
