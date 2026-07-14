from __future__ import annotations

from trading_agent.paper_execution_models import IntentId
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEventType,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_store import (
    StoredPaperMutationEvent,
    StoredPaperMutationIntent,
)


def inactive_entry_intent_ids(
    intents: tuple[StoredPaperMutationIntent, ...],
    events: tuple[StoredPaperMutationEvent, ...],
) -> frozenset[IntentId]:
    inactive: set[IntentId] = set()
    for stored in intents:
        entry_id = stored.intent.entry_intent_id
        if stored.intent.operation is not PaperMutationOperation.SUBMIT_ENTRY or entry_id is None:
            continue
        matching = tuple(event.event for event in events if event.mutation_key == stored.mutation_key)
        if not matching or matching[-1].event_type in {
            PaperMutationEventType.REJECTED,
            PaperMutationEventType.RECOVERED_ABSENT,
        }:
            inactive.add(entry_id)
    return frozenset(inactive)
