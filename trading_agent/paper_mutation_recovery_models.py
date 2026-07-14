from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from trading_agent.paper_execution_models import BrokerOrderId
from trading_agent.paper_mutation_keys import PaperMutationKey
from trading_agent.paper_stream_recovery import PaperRecoveryState


class PaperMutationRecoveryState(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    ABSENT = "absent"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class PaperMutationRecoverySnapshot:
    connection_epoch: str
    started_at: dt.datetime
    completed_at: dt.datetime
    state: PaperRecoveryState


@dataclass(frozen=True, slots=True)
class PaperMutationRecoveryResult:
    mutation_key: PaperMutationKey
    state: PaperMutationRecoveryState
    broker_order_id: BrokerOrderId | None
