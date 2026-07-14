from __future__ import annotations

import hashlib
import json
from typing import NewType

from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationIntent,
)

PaperMutationKey = NewType("PaperMutationKey", str)
PaperMutationEventKey = NewType("PaperMutationEventKey", str)


def paper_mutation_key(intent: PaperMutationIntent) -> PaperMutationKey:
    material = (
        intent.account_fingerprint,
        intent.created_at.isoformat(),
        intent.operation.value,
        intent.protective_plan_key,
        intent.safety_plan_key,
        intent.action_sequence,
        intent.request_sha256,
        intent.symbol,
        intent.broker_order_id,
        None if intent.side is None else intent.side.value,
        None if intent.quantity is None else str(intent.quantity),
    )
    return PaperMutationKey(_sha256(material))


def paper_mutation_event_key(
    mutation_key: PaperMutationKey,
    event: PaperMutationEvent,
) -> PaperMutationEventKey:
    material = (
        mutation_key,
        event.attempt_number,
        event.occurred_at.isoformat(),
        event.event_type.value,
        event.request_id,
        event.status_code,
        event.broker_order_id,
        event.evidence_sha256,
    )
    return PaperMutationEventKey(_sha256(material))


def _sha256(material: tuple[str | int | None, ...]) -> str:
    encoded = json.dumps(material, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
