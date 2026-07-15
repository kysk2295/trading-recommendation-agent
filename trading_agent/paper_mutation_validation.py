from __future__ import annotations

from typing import assert_never

from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
    PaperMutationIntent,
    PaperMutationOperation,
)


class InvalidPaperMutationRecordError(RuntimeError):
    def __str__(self) -> str:
        return "Paper mutation 원장 record가 안전 계약과 일치하지 않습니다"


def require_mutation_intent(intent: PaperMutationIntent) -> None:
    quantity_valid = (
        intent.quantity is not None
        and intent.quantity.is_finite()
        and intent.quantity > 0
        and intent.quantity == intent.quantity.to_integral_value()
    )
    common_invalid = (
        not _hex64(intent.account_fingerprint)
        or intent.created_at.tzinfo is None
        or intent.created_at.utcoffset() is None
        or not _hex64(intent.request_sha256)
        or not intent.symbol
        or intent.symbol != intent.symbol.upper()
        or len(intent.symbol) > 16
    )
    match intent.operation:
        case PaperMutationOperation.SUBMIT_ENTRY:
            shape_invalid = (
                not intent.entry_intent_id
                or intent.protective_plan_key is not None
                or intent.safety_plan_key is not None
                or intent.action_sequence is not None
                or intent.broker_order_id is not None
                or intent.side is None
                or not quantity_valid
            )
        case PaperMutationOperation.SUBMIT_PROTECTIVE_OCO:
            shape_invalid = (
                not _optional_hex64(intent.protective_plan_key)
                or intent.entry_intent_id is not None
                or intent.safety_plan_key is not None
                or intent.action_sequence is not None
                or intent.broker_order_id is not None
                or intent.side is None
                or not quantity_valid
            )
        case PaperMutationOperation.CANCEL_PROTECTIVE_OCO:
            shape_invalid = (
                intent.entry_intent_id is not None
                or not _optional_hex64(intent.protective_plan_key)
                or intent.safety_plan_key is not None
                or intent.action_sequence is not None
                or not intent.broker_order_id
                or intent.side is not None
                or intent.quantity is not None
            )
        case PaperMutationOperation.CANCEL_ORDER:
            shape_invalid = (
                intent.entry_intent_id is not None
                or intent.protective_plan_key is not None
                or not _optional_hex64(intent.safety_plan_key)
                or intent.action_sequence is None
                or intent.action_sequence < 0
                or not intent.broker_order_id
                or intent.side is not None
                or intent.quantity is not None
            )
        case PaperMutationOperation.CLOSE_POSITION:
            shape_invalid = (
                intent.entry_intent_id is not None
                or intent.protective_plan_key is not None
                or not _optional_hex64(intent.safety_plan_key)
                or intent.action_sequence is None
                or intent.action_sequence < 0
                or intent.broker_order_id is not None
                or intent.side is None
                or not quantity_valid
            )
        case unreachable:
            assert_never(unreachable)
    if common_invalid or shape_invalid:
        raise InvalidPaperMutationRecordError


def require_mutation_event(event: PaperMutationEvent) -> None:
    if (
        event.attempt_number <= 0
        or event.occurred_at.tzinfo is None
        or event.occurred_at.utcoffset() is None
        or not _hex64(event.evidence_sha256)
        or (
            event.request_id is not None
            and (not event.request_id or event.request_id.strip() != event.request_id or len(event.request_id) > 128)
        )
    ):
        raise InvalidPaperMutationRecordError
    match event.event_type:
        case PaperMutationEventType.ACKNOWLEDGED:
            valid_shape = (
                event.request_id is not None
                and event.status_code is not None
                and 200 <= event.status_code < 300
                and event.broker_order_id is not None
            )
        case PaperMutationEventType.RECOVERED_ACKNOWLEDGED:
            valid_shape = event.request_id is None and event.status_code is None and event.broker_order_id is not None
        case PaperMutationEventType.REJECTED:
            valid_shape = event.status_code is not None and event.status_code >= 400 and event.broker_order_id is None
        case (
            PaperMutationEventType.ATTEMPTED
            | PaperMutationEventType.AMBIGUOUS
            | PaperMutationEventType.RECOVERED_ABSENT
        ):
            valid_shape = event.request_id is None and event.status_code is None and event.broker_order_id is None
        case unreachable:
            assert_never(unreachable)
    if not valid_shape:
        raise InvalidPaperMutationRecordError


def _optional_hex64(value: str | None) -> bool:
    return value is not None and _hex64(value)


def _hex64(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
