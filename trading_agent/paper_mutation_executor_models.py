from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading_agent.paper_execution_models import BrokerOrderId
from trading_agent.paper_mutation_keys import PaperMutationKey


class PaperMutationExecutionState(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    ALREADY_ACKNOWLEDGED = "already_acknowledged"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class PaperMutationExecutionResult:
    mutation_key: PaperMutationKey
    state: PaperMutationExecutionState
    broker_order_id: BrokerOrderId | None
