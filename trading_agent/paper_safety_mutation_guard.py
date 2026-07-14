from __future__ import annotations

from trading_agent.paper_mutation_intents import safety_action_mutation_intent
from trading_agent.paper_mutation_ledger_models import PaperMutationEventType
from trading_agent.paper_mutation_store import (
    StoredPaperMutationEvent,
    StoredPaperMutationIntent,
)
from trading_agent.paper_safety_store import StoredPaperSafetyPlan

_ACKNOWLEDGED = frozenset(
    (
        PaperMutationEventType.ACKNOWLEDGED,
        PaperMutationEventType.RECOVERED_ACKNOWLEDGED,
    )
)


def repeated_acknowledged_safety_action_reasons(
    current: StoredPaperSafetyPlan,
    plans: tuple[StoredPaperSafetyPlan, ...],
    intents: tuple[StoredPaperMutationIntent, ...],
    events: tuple[StoredPaperMutationEvent, ...],
) -> tuple[str, ...]:
    current_requests = frozenset(
        (mutation.operation, mutation.request_sha256)
        for sequence, action in enumerate(current.plan.actions)
        for mutation in (safety_action_mutation_intent(current, sequence, action),)
    )
    for prior in intents:
        intent = prior.intent
        if intent.safety_plan_key is None or intent.safety_plan_key == current.plan_key:
            continue
        source = tuple(plan for plan in plans if plan.plan_key == intent.safety_plan_key)
        if len(source) != 1:
            return ("이전 Paper 안전조치 mutation의 source 계획이 유일하지 않습니다",)
        if source[0].plan.session_date != current.plan.session_date:
            continue
        if (intent.operation, intent.request_sha256) not in current_requests:
            continue
        history = tuple(event.event for event in events if event.mutation_key == prior.mutation_key)
        if history and history[-1].event_type in _ACKNOWLEDGED:
            return ("동일 거래일에 이미 승인된 안전조치가 broker snapshot에 남아 있어 재실행을 차단합니다",)
    return ()
