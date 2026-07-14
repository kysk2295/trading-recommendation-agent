from __future__ import annotations

from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
)


class InvalidPaperMutationTransitionError(RuntimeError):
    def __str__(self) -> str:
        return "Paper mutation event 전이가 불완전하거나 재시도에 안전하지 않습니다"


def require_mutation_transition(
    existing: tuple[PaperMutationEvent, ...],
    candidate: PaperMutationEvent,
) -> None:
    same_attempt = tuple(event for event in existing if event.attempt_number == candidate.attempt_number)
    if candidate.event_type is PaperMutationEventType.ATTEMPTED:
        _require_new_attempt(existing, same_attempt, candidate.attempt_number)
        return
    if not any(event.event_type is PaperMutationEventType.ATTEMPTED for event in same_attempt):
        raise InvalidPaperMutationTransitionError
    if candidate.event_type in (
        PaperMutationEventType.ACKNOWLEDGED,
        PaperMutationEventType.REJECTED,
        PaperMutationEventType.AMBIGUOUS,
    ):
        if any(event.event_type is not PaperMutationEventType.ATTEMPTED for event in same_attempt):
            raise InvalidPaperMutationTransitionError
        return
    if not any(event.event_type is PaperMutationEventType.AMBIGUOUS for event in same_attempt) or any(
        event.event_type
        in (
            PaperMutationEventType.RECOVERED_ACKNOWLEDGED,
            PaperMutationEventType.RECOVERED_ABSENT,
        )
        for event in same_attempt
    ):
        raise InvalidPaperMutationTransitionError


def _require_new_attempt(
    existing: tuple[PaperMutationEvent, ...],
    same_attempt: tuple[PaperMutationEvent, ...],
    attempt_number: int,
) -> None:
    if same_attempt:
        raise InvalidPaperMutationTransitionError
    latest_attempt = max(
        (event.attempt_number for event in existing),
        default=0,
    )
    if attempt_number != latest_attempt + 1:
        raise InvalidPaperMutationTransitionError
    if latest_attempt == 0:
        return
    previous = tuple(event for event in existing if event.attempt_number == latest_attempt)
    if not any(event.event_type is PaperMutationEventType.RECOVERED_ABSENT for event in previous):
        raise InvalidPaperMutationTransitionError
